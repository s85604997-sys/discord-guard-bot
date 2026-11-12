import json
import os
import random
import re
import time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
TOKEN = os.getenv("DISCORD_TOKEN")
GUARD_CHANNEL_ID = int(os.getenv("GUARD_CHANNEL_ID", "1570087256050507956"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
AUTO_ROLE_ID = int(os.getenv("AUTO_ROLE_ID", "0"))

with (BASE_DIR / "guard_config.json").open("r", encoding="utf-8") as file:
    WORDS = json.load(file)

GUARD_CONFIG_PATH = BASE_DIR / "guard_config.json"


def load_guard_config() -> dict:
    if GUARD_CONFIG_PATH.exists():
        with GUARD_CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "forbidden_words": [],
        "smart_filter": True,
        "block_links": True,
        "block_invites": True,
        "max_caps_percent": 80,
        "min_caps_length": 8,
        "spam_messages_limit": 5,
        "spam_seconds_window": 6,
        "max_warnings": 3,
        "mute_minutes_on_max_warnings": 10,
    }


def save_guard_config(config: dict) -> None:
    with GUARD_CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


guard_config = load_guard_config()
user_message_timestamps: dict[int, list[float]] = defaultdict(list)
user_warnings: dict[int, int] = defaultdict(int)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

INVITE_REGEX = re.compile(r"(discord\.gg/|discord\.com/invite/|discordapp\.com/invite/)", re.IGNORECASE)
URL_REGEX = re.compile(r"https?://\S+", re.IGNORECASE)


def stylize(name: str) -> str:
    if random.random() < 0.16:
        replacements = {"a": "4", "e": "3", "i": "1", "o": "0", "s": "5"}
        choices = [i for i, char in enumerate(name) if char in replacements]
        if choices:
            index = random.choice(choices)
            name = name[:index] + replacements[name[index]] + name[index + 1:]
    return name.lower()


def make_nickname() -> str:
    for _ in range(100):
        count = random.choices([1, 2, 3], weights=[12, 78, 10], k=1)[0]
        chosen = random.sample(WORDS, count)
        candidate = stylize("".join(chosen))
        if 3 <= len(candidate) <= 15 and re.fullmatch(r"[a-z0-9]+", candidate):
            return candidate
    return "lost" + str(random.randint(100, 999))


def make_batch(amount: int = 15) -> list[str]:
    result = []
    seen = set()
    attempts = 0
    while len(result) < amount and attempts < 2000:
        attempts += 1
        nick = make_nickname()
        if nick not in seen:
            seen.add(nick)
            result.append(nick)
    return result


def make_embed(nicknames: list[str]) -> discord.Embed:
    embed = discord.Embed(
        title="✦ NICK GENERATOR",
        description="```text\n" + "\n".join(nicknames) + "\n```",
        color=discord.Color.from_rgb(132, 83, 230),
    )
    embed.set_footer(text=f"Згенеровано: {len(nicknames)} • максимум 15 символів")
    return embed


class NickView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Ще 15", style=discord.ButtonStyle.primary, emoji="🔄")
    async def again(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=make_embed(make_batch(15)), view=self)


HOMOGLYPHS = str.maketrans({
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "y": "у",
    "x": "х", "k": "к", "m": "м", "t": "т", "b": "в", "h": "н",
    "i": "і", "ё": "е", "0": "о", "3": "е", "4": "ч", "6": "б",
    "@": "а", "$": "с",
})


def normalize_text(text: str) -> tuple[str, str]:
    lowered = text.lower().translate(HOMOGLYPHS)
    collapsed = re.sub(r"[\s._\-*~|/\\]+", "", lowered)
    return lowered, collapsed


def check_violation(content: str) -> str | None:
    lowered, collapsed = normalize_text(content)
    smart = guard_config.get("smart_filter", True)

    for word in guard_config.get("forbidden_words", []):
        w_lower, w_collapsed = normalize_text(word)
        if w_lower in lowered:
            return "образи / заборонена лексика"
        if smart and len(w_collapsed) >= 4 and w_collapsed in collapsed:
            return "образи / обхід фільтра"

    if guard_config.get("block_invites", True) and INVITE_REGEX.search(content):
        return "реклама/інвайт Discord-сервера"

    if guard_config.get("block_links", True) and URL_REGEX.search(content):
        return "публікація посилань заборонена"

    letters = [c for c in content if c.isalpha()]
    min_len = guard_config.get("min_caps_length", 8)
    max_pct = guard_config.get("max_caps_percent", 80)
    if len(letters) >= min_len:
        upper_count = sum(1 for c in letters if c.isupper())
        if (upper_count / len(letters)) * 100 >= max_pct:
            return "занадто багато КАПСУ"

    return None


async def send_log(guild: discord.Guild, text: str):
    if LOG_CHANNEL_ID:
        channel = guild.get_channel(LOG_CHANNEL_ID)
        if channel:
            try:
                await channel.send(text)
            except discord.HTTPException:
                pass


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Синхронізовано slash-команд: {len(synced)}")
    except Exception as error:
        print(f"Помилка синхронізації команд: {error}")
    print(f"Бот запущений: {bot.user} (ID: {bot.user.id})")
    print(f"Chat Guard активний для каналу ID: {GUARD_CHANNEL_ID}")


@bot.event
async def on_member_join(member: discord.Member):
    if AUTO_ROLE_ID:
        role = member.guild.get_role(AUTO_ROLE_ID)
        if role:
            try:
                await member.add_roles(role, reason="Chat Guard: Автороль при вході")
            except discord.Forbidden:
                print("Недостатньо прав для видачі авторолі!")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    if message.channel.id == GUARD_CHANNEL_ID:
        is_admin = isinstance(message.author, discord.Member) and message.author.guild_permissions.manage_messages
        if not is_admin:
            now = time.time()
            window = guard_config.get("spam_seconds_window", 6)
            limit = guard_config.get("spam_messages_limit", 5)

            timestamps = user_message_timestamps[message.author.id]
            timestamps.append(now)
            user_message_timestamps[message.author.id] = [t for t in timestamps if now - t <= window]

            if len(user_message_timestamps[message.author.id]) >= limit:
                user_message_timestamps[message.author.id].clear()
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass

                mute_mins = guard_config.get("mute_minutes_on_max_warnings", 10)
                try:
                    if isinstance(message.author, discord.Member):
                        await message.author.timeout(
                            timedelta(minutes=mute_mins),
                            reason="Chat Guard: флуд у чаті",
                        )
                    warn = await message.channel.send(
                        f"🛡️ **Chat Guard:** {message.author.mention} отримав тайм-аут на {mute_mins} хв. за флуд."
                    )
                    await send_log(message.guild, f"⚠️ **ФЛУД:** {message.author} отримав мут на {mute_mins} хв.")
                    await warn.delete(delay=7)
                except discord.Forbidden:
                    warn = await message.channel.send(
                        f"🛡️ **Chat Guard:** {message.author.mention}, припиніть флудити!"
                    )
                    await warn.delete(delay=5)
                return

            reason = check_violation(message.content)
            if reason:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass

                user_id = message.author.id
                user_warnings[user_id] += 1
                current_warns = user_warnings[user_id]
                max_warns = guard_config.get("max_warnings", 3)

                if current_warns >= max_warns:
                    user_warnings[user_id] = 0
                    mute_mins = guard_config.get("mute_minutes_on_max_warnings", 10)
                    try:
                        if isinstance(message.author, discord.Member):
                            await message.author.timeout(
                                timedelta(minutes=mute_mins),
                                reason=f"Chat Guard: ліміт попереджень ({max_warns}/{max_warns})",
                            )
                        warn = await message.channel.send(
                            f"🛡️ **Chat Guard:** {message.author.mention} отримав мут на {mute_mins} хв. за порушення ({max_warns}/{max_warns} попереджень)."
                        )
                        await send_log(message.guild, f"🔴 **МУТ ЗА ПОПЕРЕДЖЕННЯ:** {message.author} замучений на {mute_mins} хв.")
                        await warn.delete(delay=8)
                    except discord.Forbidden:
                        warn = await message.channel.send(
                            f"🛡️ **Chat Guard:** {message.author.mention}, вичерпано ліміт попереджень!"
                        )
                        await warn.delete(delay=6)
                else:
                    try:
                        warn = await message.channel.send(
                            f"🛡️ **Chat Guard:** {message.author.mention}, повідомлення видалено ({reason}). Попередження **{current_warns}/{max_warns}**."
                        )
                        await send_log(message.guild, f"🟡 **ПОПЕРЕДЖЕННЯ ({current_warns}/{max_warns}):** {message.author} порушив правила в каналі <#{message.channel.id}>.")
                        await warn.delete(delay=6)
                    except discord.HTTPException:
                        pass
                return

    await bot.process_commands(message)


# --- SLASH-КОМАНДА ГЕНЕРАТОРА НІКІВ ---

@bot.tree.command(name="nick", description="Згенерувати 15 випадкових нікнеймів")
async def nick(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=make_embed(make_batch(15)),
        view=NickView(),
        ephemeral=False,
    )


# --- SLASH-КОМАНДИ МОДЕРАЦІЇ ---

@bot.tree.command(name="clear", description="Очистити останні повідомлення в чаті")
@app_commands.describe(
    amount="Скільки повідомлень видалити (від 1 до 100)",
    member="Видалити повідомлення лише від конкретного учасника (необов'язково)"
)
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(
    interaction: discord.Interaction,
    amount: app_commands.Range[int, 1, 100],
    member: discord.Member | None = None,
):
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ Цю команду можна використовувати лише в текстовому каналі.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    def check(msg: discord.Message) -> bool:
        if member is not None:
            return msg.author.id == member.id
        return True

    try:
        deleted = await interaction.channel.purge(limit=amount, check=check)
        target_text = f" від **{member}**" if member else ""
        await interaction.followup.send(
            f"🧹 Видалено повідомлень{target_text}: **{len(deleted)}**.",
            ephemeral=True,
        )
        await send_log(
            interaction.guild,
            f"🧹 **ОЧИЩЕННЯ ЧАТУ:** {interaction.user} видалив {len(deleted)} повід.{target_text} у <#{interaction.channel.id}>.",
        )
    except discord.Forbidden:
        await interaction.followup.send("❌ У бота немає прав на видалення повідомлень у цьому каналі!", ephemeral=True)
    except discord.HTTPException as error:
        await interaction.followup.send(f"❌ Помилка під час очищення чату: {error}", ephemeral=True)


@bot.tree.command(name="ban", description="Заблокувати учасника на сервері")
@app_commands.describe(member="Кого заблокувати", reason="Причина бану")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "Причина не вказана"):
    try:
        await member.ban(reason=reason)
        await interaction.response.send_message(f"🔨 Користувача **{member}** заблоковано. Причина: `{reason}`", ephemeral=True)
        await send_log(interaction.guild, f"🔨 **БАН:** Адміністратор {interaction.user} заблокував {member}. Причина: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("❌ У мене немає прав на блокування цього користувача!", ephemeral=True)


@bot.tree.command(name="kick", description="Вигнати учасника з сервера")
@app_commands.describe(member="Кого вигнати", reason="Причина кіку")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "Причина не вказана"):
    try:
        await member.kick(reason=reason)
        await interaction.response.send_message(f"👢 Користувача **{member}** вигнано з сервера. Причина: `{reason}`", ephemeral=True)
        await send_log(interaction.guild, f"👢 **КІК:** Адміністратор {interaction.user} вигнав {member}. Причина: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("❌ У мене немає прав на вигнання цього користувача!", ephemeral=True)


@bot.tree.command(name="mute", description="Видати тайм-аут (мут) учаснику")
@app_commands.describe(member="Кого замутити", minutes="Тривалість у хвилинах", reason="Причина муту")
@app_commands.checks.has_permissions(moderate_members=True)
async def mute(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "Причина не вказана"):
    try:
        await member.timeout(timedelta(minutes=minutes), reason=reason)
        await interaction.response.send_message(f"🔇 Користувач **{member}** отримав мут на **{minutes} хв.** Причина: `{reason}`", ephemeral=True)
        await send_log(interaction.guild, f"🔇 **МУТ:** Адміністратор {interaction.user} замутив {member} на {minutes} хв. Причина: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("❌ У мене немає прав на видачу муту цьому користувачу!", ephemeral=True)


@bot.tree.command(name="unmute", description="Зняти тайм-аут (мут) з учасника")
@app_commands.describe(member="З кого зняти мут")
@app_commands.checks.has_permissions(moderate_members=True)
async def unmute(interaction: discord.Interaction, member: discord.Member):
    try:
        await member.timeout(None, reason="Знято мут модератором")
        await interaction.response.send_message(f"🔊 З користувача **{member}** знято мут.", ephemeral=True)
        await send_log(interaction.guild, f"🔊 **РОЗМУТ:** Адміністратор {interaction.user} зняв мут з {member}.")
    except discord.Forbidden:
        await interaction.response.send_message("❌ У мене немає прав керувати мутом цього користувача!", ephemeral=True)


# --- КЕРУВАННЯ ФІЛЬТРОМ (ДОДАВАННЯ ПАЧКОЮ ЧЕРЕЗ КОМУ) ---

@bot.tree.command(name="guard_add", description="Додати слова до фільтра через кому (наприклад: слово1, слово2)")
@app_commands.describe(words="Слова чи фрази через кому")
@app_commands.checks.has_permissions(manage_messages=True)
async def guard_add(interaction: discord.Interaction, words: str):
    # Розбиваємо введений рядок за комою
    new_words = [w.strip().lower() for w in words.split(",") if w.strip()]
    if not new_words:
        await interaction.response.send_message("❌ Ви не вказали жодного слова!", ephemeral=True)
        return

    added_count = 0
    existing_list = [w.lower() for w in guard_config["forbidden_words"]]

    for w in new_words:
        if w not in existing_list:
            guard_config["forbidden_words"].append(w)
            existing_list.append(w)
            added_count += 1

    save_guard_config(guard_config)
    await interaction.response.send_message(
        f"🛡️ Успішно додано нових слів/фраз: **{added_count}** (всього у базі: {len(guard_config['forbidden_words'])}).",
        ephemeral=True
    )


@bot.tree.command(name="guard_remove", description="Видалити слово з фільтра Chat Guard")
@app_commands.describe(word="Слово для видалення")
@app_commands.checks.has_permissions(manage_messages=True)
async def guard_remove(interaction: discord.Interaction, word: str):
    word_lower = word.strip().lower()
    before = len(guard_config["forbidden_words"])
    guard_config["forbidden_words"] = [w for w in guard_config["forbidden_words"] if w.lower() != word_lower]
    if len(guard_config["forbidden_words"]) == before:
        await interaction.response.send_message(f"Слово `{word}` не знайдено у списку.", ephemeral=True)
        return
    save_guard_config(guard_config)
    await interaction.response.send_message(f"🛡️ Слово `{word}` видалено з фільтра.", ephemeral=True)


@bot.tree.command(name="guard_list", description="Показати налаштування Chat Guard")
@app_commands.checks.has_permissions(manage_messages=True)
async def guard_list(interaction: discord.Interaction):
    words_list = guard_config["forbidden_words"]
    words_str = ""
    for w in words_list:
        next_chunk = f"`{w}`, "
        if len(words_str) + len(next_chunk) > 1000:
            words_str += "..."
            break
        words_str += next_chunk
    words_str = words_str.rstrip(", ") or "порожньо"

    embed = discord.Embed(title="🛡️ Налаштування Chat Guard", color=discord.Color.from_rgb(230, 83, 83))
    embed.add_field(name="Охоронюваний канал", value=f"<#{GUARD_CHANNEL_ID}> (`{GUARD_CHANNEL_ID}`)", inline=False)
    embed.add_field(name=f"Заборонені слова (всього: {len(words_list)})", value=words_str, inline=False)
    embed.add_field(
        name="Фільтри",
        value=(
            f"• Ліміт попереджень: **{guard_config.get('max_warnings', 3)}** (мут {guard_config.get('mute_minutes_on_max_warnings', 10)} хв.)\n"
            f"• Анти-флуд: **{guard_config.get('spam_messages_limit', 5)} повід. / {guard_config.get('spam_seconds_window', 6)} сек.**"
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Не знайдено токен! Перевір файл .env.")
    bot.run(TOKEN)
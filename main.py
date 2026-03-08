import discord
from discord import app_commands
from discord.ext import commands, tasks
import yt_dlp
import asyncio
import random
import time
import os
from collections import deque
from dotenv import load_dotenv

load_dotenv()

# ===== BOT SETUP =====
intents = discord.Intents.default()
intents.message_content = True

class MusicBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print("✅ Slash commands synced")

bot = MusicBot()

# ===== PER-GUILD STATE =====
class GuildState:
    def __init__(self):
        self.queue: deque = deque()
        self.now_playing: dict = None
        self.loop_mode: str = "off"
        self.volume: float = 0.5
        self.start_time: float = None
        self.panel_message: discord.Message = None
        self.panel_channel_id: int = None   # channel ที่ /setup ไว้

states: dict[int, GuildState] = {}

def get_state(guild_id: int) -> GuildState:
    if guild_id not in states:
        states[guild_id] = GuildState()
    return states[guild_id]

# ===== HELPERS =====
def format_duration(seconds):
    if not seconds:
        return "🔴 LIVE"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def build_progress_bar(elapsed: int, total: int, length: int = 22) -> str:
    if not total:
        return "🔴 LIVE"
    filled = min(int(length * elapsed / total), length)
    bar = "─" * filled + "⬤" + "─" * (length - filled)
    return f"`{bar}`"

def extract_info(query: str) -> list:
    opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'noplaylist': False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        if query.startswith("http"):
            info = ydl.extract_info(query, download=False)
        else:
            info = ydl.extract_info(f"ytsearch:{query}", download=False)
        if 'entries' in info:
            return [e for e in info['entries'] if e]
        return [info]

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

# ===== PANEL EMBED =====
def build_panel_embed(state: GuildState) -> discord.Embed:
    track = state.now_playing

    if not track:
        e = discord.Embed(
            title="🎵 ห้องเล่นเพลง | Music Room",
            description='วางลิ้งค์หรือชื่อเพลงในช่องนี้เพื่อเพิ่มเข้า queue\nหรือใช้คำสั่ง `/play`',
            color=0x2B2D31
        )
        e.add_field(name="⚡ สถานะ", value="ขณะนี้ไม่มีเพลงที่กำลังเล่นอยู่", inline=False)
        e.set_footer(text="[/] Powered by Kaniva Music Bot")
        return e

    elapsed = int(time.time() - state.start_time) if state.start_time else 0
    total = track.get('duration') or 0
    elapsed = min(elapsed, total) if total else elapsed

    progress = build_progress_bar(elapsed, total)
    time_str = f"`{format_duration(elapsed)} / {format_duration(total)}`"
    loop_icons = {"off": "➡️ Off", "one": "🔂 One", "all": "🔁 All"}
    queue_len = len(state.queue)

    e = discord.Embed(
        title="🎵 ห้องเล่นเพลง | Music Room",
        description=f"### [{track['title']}]({track['webpage_url']})",
        color=0x1DB954
    )
    e.add_field(name="\u200b", value=f"{progress}\n{time_str}", inline=False)
    e.add_field(name="⏱ ความยาว", value=format_duration(total), inline=True)
    e.add_field(name="🔊 Volume", value=f"{int(state.volume*100)}%", inline=True)
    e.add_field(name="🔁 Loop", value=loop_icons[state.loop_mode], inline=True)
    e.add_field(name="👤 ขอโดย", value=f"@{track['requester']}", inline=True)
    e.add_field(name="📋 Queue", value=f"{queue_len} Songs", inline=True)

    if queue_len > 0:
        next_tracks = list(state.queue)[:3]
        next_lines = "\n".join([f"`{i+1}.` {t['title']}" for i, t in enumerate(next_tracks)])
        if queue_len > 3:
            next_lines += f"\n`...และอีก {queue_len-3} เพลง`"
        e.add_field(name="⏭️ ถัดไป", value=next_lines, inline=False)

    if track.get('thumbnail'):
        e.set_image(url=track['thumbnail'])

    e.set_footer(text="[/] Powered by Kaniva Music Bot")
    return e

# ===== PANEL VIEW =====
class PanelView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        state = get_state(guild_id)
        # sync loop button
        loop_labels = {"off": ("➡️", "Loop: Off"), "one": ("🔂", "Loop: One"), "all": ("🔁", "Loop: All")}
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == "loop_btn":
                item.emoji = loop_labels[state.loop_mode][0]
                item.label = loop_labels[state.loop_mode][1]
        # ถ้าไม่มีเพลง ซ่อนปุ่มที่ไม่จำเป็น
        if not state.now_playing:
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.custom_id in ("pause_resume", "skip_btn", "stop_btn", "shuffle_btn", "vol_down", "vol_up", "loop_btn", "queue_btn"):
                    item.disabled = True

    async def refresh(self, interaction: discord.Interaction = None):
        state = get_state(self.guild_id)
        # re-enable/disable ปุ่มตาม state
        has_track = state.now_playing is not None
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id in ("pause_resume", "skip_btn", "stop_btn", "shuffle_btn", "vol_down", "vol_up", "loop_btn", "queue_btn"):
                item.disabled = not has_track
        try:
            if state.panel_message:
                await state.panel_message.edit(embed=build_panel_embed(state), view=self)
        except Exception:
            pass

    @discord.ui.button(emoji="⏸️", label="Pause", style=discord.ButtonStyle.secondary, custom_id="pause_resume", row=0)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        vc = interaction.guild.voice_client
        if not vc:
            return
        if vc.is_playing():
            vc.pause()
            button.emoji = "▶️"
            button.label = "Resume"
        elif vc.is_paused():
            vc.resume()
            button.emoji = "⏸️"
            button.label = "Pause"
        await self.refresh()

    @discord.ui.button(emoji="⏭️", label="Skip", style=discord.ButtonStyle.primary, custom_id="skip_btn", row=0)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()

    @discord.ui.button(emoji="⏹️", label="Stop", style=discord.ButtonStyle.danger, custom_id="stop_btn", row=0)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        state = get_state(self.guild_id)
        vc = interaction.guild.voice_client
        if vc:
            state.queue.clear()
            state.now_playing = None
            state.start_time = None
            vc.stop()
            await vc.disconnect()
        await self.refresh()

    @discord.ui.button(emoji="🔀", label="Shuffle", style=discord.ButtonStyle.secondary, custom_id="shuffle_btn", row=0)
    async def shuffle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        state = get_state(self.guild_id)
        if len(state.queue) >= 2:
            lst = list(state.queue)
            random.shuffle(lst)
            state.queue = deque(lst)
        await self.refresh()

    @discord.ui.button(emoji="➡️", label="Loop: Off", style=discord.ButtonStyle.secondary, custom_id="loop_btn", row=1)
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        state = get_state(self.guild_id)
        modes = ["off", "one", "all"]
        labels = {"off": ("➡️", "Loop: Off"), "one": ("🔂", "Loop: One"), "all": ("🔁", "Loop: All")}
        state.loop_mode = modes[(modes.index(state.loop_mode) + 1) % len(modes)]
        button.emoji = labels[state.loop_mode][0]
        button.label = labels[state.loop_mode][1]
        await self.refresh()

    @discord.ui.button(emoji="🔉", label="Vol -10%", style=discord.ButtonStyle.secondary, custom_id="vol_down", row=1)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        state = get_state(self.guild_id)
        state.volume = max(0.0, round(state.volume - 0.1, 1))
        vc = interaction.guild.voice_client
        if vc and vc.source:
            vc.source.volume = state.volume
        await self.refresh()

    @discord.ui.button(emoji="🔊", label="Vol +10%", style=discord.ButtonStyle.secondary, custom_id="vol_up", row=1)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        state = get_state(self.guild_id)
        state.volume = min(2.0, round(state.volume + 0.1, 1))
        vc = interaction.guild.voice_client
        if vc and vc.source:
            vc.source.volume = state.volume
        await self.refresh()

    @discord.ui.button(emoji="📋", label="Queue", style=discord.ButtonStyle.secondary, custom_id="queue_btn", row=1)
    async def queue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        q = list(state.queue)
        if not q:
            return await interaction.response.send_message("📋 Queue ว่างเปล่า", ephemeral=True)
        lines = [f"`{i+1}.` {t['title']} `{format_duration(t['duration'])}`" for i, t in enumerate(q[:15])]
        if len(q) > 15:
            lines.append(f"...และอีก {len(q)-15} เพลง")
        e = discord.Embed(title=f"📋 Queue ({len(q)} เพลง)", description="\n".join(lines), color=0x1DB954)
        await interaction.response.send_message(embed=e, ephemeral=True)

# ===== PLAY NEXT =====
async def play_next(guild: discord.Guild, vc: discord.VoiceClient):
    state = get_state(guild.id)

    if state.loop_mode == "one" and state.now_playing:
        track = state.now_playing
    elif state.queue:
        track = state.queue.popleft()
        if state.loop_mode == "all":
            state.queue.append(track)
        state.now_playing = track
    else:
        state.now_playing = None
        state.start_time = None
        view = PanelView(guild.id)
        if state.panel_message:
            try:
                await state.panel_message.edit(embed=build_panel_embed(state), view=view)
            except Exception:
                pass
        return

    try:
        source = discord.FFmpegPCMAudio(track['url'], **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=state.volume)

        def after(error):
            if error:
                print(f"Player error: {error}")
            asyncio.run_coroutine_threadsafe(play_next(guild, vc), bot.loop)

        vc.play(source, after=after)
        state.start_time = time.time()

        view = PanelView(guild.id)
        if state.panel_message:
            try:
                await state.panel_message.edit(embed=build_panel_embed(state), view=view)
            except Exception:
                pass

    except Exception as e:
        print(f"Error playing: {e}")
        await play_next(guild, vc)

# ===== BACKGROUND: อัปเดต progress bar =====
@tasks.loop(seconds=5)
async def update_panel_task():
    for guild_id, state in states.items():
        if state.panel_message and state.now_playing:
            try:
                view = PanelView(guild_id)
                await state.panel_message.edit(embed=build_panel_embed(state), view=view)
            except Exception:
                pass

@update_panel_task.before_loop
async def before_update():
    await bot.wait_until_ready()

# ===== AUTO-QUEUE: รับ URL/ชื่อเพลงในช่องที่ setup =====
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    state = get_state(message.guild.id) if message.guild else None

    if state and message.channel.id == state.panel_channel_id:
        query = message.content.strip()
        if query:
            # ลบ message ของ user
            try:
                await message.delete()
            except Exception:
                pass

            if not message.author.voice:
                err = await message.channel.send(f"❌ {message.author.mention} เข้า voice channel ก่อนนะ!", delete_after=5)
                return

            vc = message.guild.voice_client
            if vc is None:
                vc = await message.author.voice.channel.connect()
            elif vc.channel != message.author.voice.channel:
                await vc.move_to(message.author.voice.channel)

            loading = await message.channel.send(f"🔍 กำลังหา: `{query[:50]}`", delete_after=10)

            loop = asyncio.get_event_loop()
            try:
                tracks = await loop.run_in_executor(None, extract_info, query)
            except Exception as e:
                await message.channel.send(f"❌ Error: {e}", delete_after=8)
                return

            for t in tracks:
                state.queue.append({
                    'title': t.get('title', 'Unknown'),
                    'url': t.get('url') or t.get('webpage_url'),
                    'duration': t.get('duration'),
                    'webpage_url': t.get('webpage_url', ''),
                    'thumbnail': t.get('thumbnail', ''),
                    'requester': message.author.display_name,
                })

            if not vc.is_playing() and not vc.is_paused():
                await play_next(message.guild, vc)
            else:
                view = PanelView(message.guild.id)
                if state.panel_message:
                    try:
                        await state.panel_message.edit(embed=build_panel_embed(state), view=view)
                    except Exception:
                        pass

        return  # ไม่ process commands ในช่องนี้

    await bot.process_commands(message)

# ===== EVENTS =====
@bot.event
async def on_ready():
    update_panel_task.start()
    print(f"✅ Bot ready: {bot.user}")

# ===== SLASH COMMANDS =====
@bot.tree.command(name="setup", description="ตั้ง music panel ในช่องที่เลือก")
@app_commands.describe(channel="channel ที่จะใช้เป็น music room")
async def setup_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
    state = get_state(interaction.guild_id)

    # ลบ panel เก่า
    if state.panel_message:
        try:
            await state.panel_message.delete()
        except Exception:
            pass

    state.panel_channel_id = channel.id

    view = PanelView(interaction.guild_id)
    panel_msg = await channel.send(embed=build_panel_embed(state), view=view)
    state.panel_message = panel_msg

    await interaction.response.send_message(
        f"✅ ตั้ง music room ที่ {channel.mention} แล้ว\nวางลิ้งค์หรือชื่อเพลงในช่องนั้นเพื่อเพิ่มเข้า queue ได้เลย",
        ephemeral=True
    )

@bot.tree.command(name="play", description="เล่นเพลงหรือเพิ่มเข้า queue")
@app_commands.describe(query="ชื่อเพลงหรือ URL")
async def play(interaction: discord.Interaction, query: str):
    if not interaction.user.voice:
        return await interaction.response.send_message("❌ เข้า voice channel ก่อนนะ!", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    vc = interaction.guild.voice_client
    if vc is None:
        vc = await interaction.user.voice.channel.connect()
    elif vc.channel != interaction.user.voice.channel:
        await vc.move_to(interaction.user.voice.channel)

    state = get_state(interaction.guild_id)

    try:
        loop = asyncio.get_event_loop()
        tracks = await loop.run_in_executor(None, extract_info, query)
    except Exception as e:
        return await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    for t in tracks:
        state.queue.append({
            'title': t.get('title', 'Unknown'),
            'url': t.get('url') or t.get('webpage_url'),
            'duration': t.get('duration'),
            'webpage_url': t.get('webpage_url', ''),
            'thumbnail': t.get('thumbnail', ''),
            'requester': interaction.user.display_name,
        })

    msg = f"✅ เพิ่ม **{tracks[0]['title']}**" if len(tracks) == 1 else f"✅ เพิ่ม **{len(tracks)} เพลง** เข้า queue"
    await interaction.followup.send(msg, ephemeral=True)

    if not vc.is_playing() and not vc.is_paused():
        await play_next(interaction.guild, vc)
    elif state.panel_message:
        view = PanelView(interaction.guild_id)
        try:
            await state.panel_message.edit(embed=build_panel_embed(state), view=view)
        except Exception:
            pass

@bot.tree.command(name="skip", description="ข้ามเพลงปัจจุบัน")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await interaction.response.send_message("⏭️ ข้ามแล้ว", ephemeral=True)
    else:
        await interaction.response.send_message("❌ ไม่มีเพลงที่กำลังเล่น", ephemeral=True)

@bot.tree.command(name="stop", description="หยุดและออกจาก voice channel")
async def stop(interaction: discord.Interaction):
    state = get_state(interaction.guild_id)
    vc = interaction.guild.voice_client
    if vc:
        state.queue.clear()
        state.now_playing = None
        state.start_time = None
        vc.stop()
        await vc.disconnect()
    await interaction.response.send_message("⏹️ หยุดแล้ว", ephemeral=True)
    if state.panel_message:
        view = PanelView(interaction.guild_id)
        try:
            await state.panel_message.edit(embed=build_panel_embed(state), view=view)
        except Exception:
            pass

@bot.tree.command(name="volume", description="ตั้ง volume 0-200")
@app_commands.describe(vol="ค่า volume (0-200)")
async def volume(interaction: discord.Interaction, vol: int):
    if not 0 <= vol <= 200:
        return await interaction.response.send_message("❌ ใส่ค่า 0-200", ephemeral=True)
    state = get_state(interaction.guild_id)
    state.volume = vol / 100
    vc = interaction.guild.voice_client
    if vc and vc.source:
        vc.source.volume = state.volume
    await interaction.response.send_message(f"🔊 Volume: {vol}%", ephemeral=True)
    if state.panel_message:
        try:
            await state.panel_message.edit(embed=build_panel_embed(state), view=PanelView(interaction.guild_id))
        except Exception:
            pass

@bot.tree.command(name="loop", description="ตั้ง loop mode")
@app_commands.describe(mode="off / one / all")
@app_commands.choices(mode=[
    app_commands.Choice(name="off - ไม่ loop", value="off"),
    app_commands.Choice(name="one - loop เพลงเดียว", value="one"),
    app_commands.Choice(name="all - loop ทั้ง queue", value="all"),
])
async def loop_cmd(interaction: discord.Interaction, mode: str):
    state = get_state(interaction.guild_id)
    state.loop_mode = mode
    icons = {"off": "➡️", "one": "🔂", "all": "🔁"}
    await interaction.response.send_message(f"{icons[mode]} Loop: **{mode}**", ephemeral=True)
    if state.panel_message:
        try:
            await state.panel_message.edit(embed=build_panel_embed(state), view=PanelView(interaction.guild_id))
        except Exception:
            pass

@bot.tree.command(name="shuffle", description="สุ่ม queue")
async def shuffle(interaction: discord.Interaction):
    state = get_state(interaction.guild_id)
    if len(state.queue) < 2:
        return await interaction.response.send_message("❌ Queue ต้องมีอย่างน้อย 2 เพลง", ephemeral=True)
    lst = list(state.queue)
    random.shuffle(lst)
    state.queue = deque(lst)
    await interaction.response.send_message(f"🔀 สุ่ม {len(lst)} เพลงแล้ว", ephemeral=True)
    if state.panel_message:
        try:
            await state.panel_message.edit(embed=build_panel_embed(state), view=PanelView(interaction.guild_id))
        except Exception:
            pass

@bot.tree.command(name="queue", description="ดู queue ปัจจุบัน")
async def queue_cmd(interaction: discord.Interaction):
    state = get_state(interaction.guild_id)
    q = list(state.queue)
    lines = []
    if state.now_playing:
        lines.append(f"**🎵 กำลังเล่น:** {state.now_playing['title']} `{format_duration(state.now_playing['duration'])}`\n")
    if not q:
        lines.append("*Queue ว่างเปล่า*")
    else:
        for i, t in enumerate(q[:20], 1):
            lines.append(f"`{i}.` {t['title']} `{format_duration(t['duration'])}`")
        if len(q) > 20:
            lines.append(f"...และอีก {len(q)-20} เพลง")
    e = discord.Embed(title=f"📋 Queue ({len(q)} เพลงถัดไป)", description="\n".join(lines), color=0x1DB954)
    await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="remove", description="ลบเพลงออกจาก queue")
@app_commands.describe(position="ตำแหน่งใน queue (1, 2, 3...)")
async def remove(interaction: discord.Interaction, position: int):
    state = get_state(interaction.guild_id)
    q = list(state.queue)
    if not 1 <= position <= len(q):
        return await interaction.response.send_message("❌ ตำแหน่งไม่ถูกต้อง", ephemeral=True)
    removed = q.pop(position - 1)
    state.queue = deque(q)
    await interaction.response.send_message(f"🗑️ ลบ **{removed['title']}** แล้ว", ephemeral=True)
    if state.panel_message:
        try:
            await state.panel_message.edit(embed=build_panel_embed(state), view=PanelView(interaction.guild_id))
        except Exception:
            pass

@bot.tree.command(name="clear", description="ล้าง queue ทั้งหมด")
async def clear(interaction: discord.Interaction):
    state = get_state(interaction.guild_id)
    state.queue.clear()
    await interaction.response.send_message("🗑️ ล้าง Queue แล้ว", ephemeral=True)
    if state.panel_message:
        try:
            await state.panel_message.edit(embed=build_panel_embed(state), view=PanelView(interaction.guild_id))
        except Exception:
            pass

@bot.tree.command(name="np", description="ดูเพลงที่กำลังเล่นอยู่")
async def np(interaction: discord.Interaction):
    state = get_state(interaction.guild_id)
    track = state.now_playing
    if not track:
        return await interaction.response.send_message("❌ ไม่มีเพลงที่กำลังเล่น", ephemeral=True)
    e = discord.Embed(title=f"🎵 {track['title']}", url=track['webpage_url'], color=0x1DB954)
    e.add_field(name="ความยาว", value=format_duration(track['duration']))
    e.add_field(name="ขอโดย", value=track['requester'])
    if track['thumbnail']:
        e.set_thumbnail(url=track['thumbnail'])
    await interaction.response.send_message(embed=e, ephemeral=True)

bot.run(os.getenv("TOKEN"))
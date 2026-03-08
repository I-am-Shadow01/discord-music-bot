# Discord Music Bot

Discord music bot using discord.py + yt-dlp

## Requirements

- Python 3.10+
- FFmpeg (must be in PATH or same folder)
- Node.js (for yt-dlp JS runtime)

## Setup

1. Clone the repo
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your token:
   ```
   TOKEN=your_token_here
   ```
4. Run:
   ```bash
   python main.py
   ```

## Commands

| Command | Description |
|---|---|
| `/play <query>` | เล่นเพลงหรือเพิ่มเข้า queue |
| `/setup` | ตั้ง music control panel |
| `/skip` | ข้ามเพลง |
| `/stop` | หยุดและออก voice |
| `/volume <0-200>` | ตั้ง volume |
| `/loop <off/one/all>` | ตั้ง loop mode |
| `/shuffle` | สุ่ม queue |
| `/queue` | ดู queue |
| `/remove <pos>` | ลบเพลงออกจาก queue |
| `/clear` | ล้าง queue |
| `/np` | ดูเพลงที่กำลังเล่น |

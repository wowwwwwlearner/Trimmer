import os
import subprocess
import logging
import asyncio
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VIDEO, TRIM, SCENE_NAMES, DESTINATION = range(4)
user_data = {}
admin_id = 5196560763  # <-- Replace with your Telegram user ID
allowed_users = {admin_id}
rclone_remote = "remote:TelegramBotUploads"  # default remote, can be changed by admin

def parse_scene_ranges(text):
    scenes = []
    pairs = text.split(",")
    for p in pairs:
        if "-" not in p:
            continue
        start, end = p.strip().split("-")
        scenes.append((start.strip(), end.strip()))
    return scenes

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in allowed_users:
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return ConversationHandler.END

    reply_keyboard = [["Telegram", "Rclone"]]
    await update.message.reply_text(
        "👋 Welcome! Please choose your upload destination:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True),
    )
    return DESTINATION

async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != admin_id:
        await update.message.reply_text("❌ Only the admin can add users.")
        return
    try:
        new_user_id = int(context.args[0])
        allowed_users.add(new_user_id)
        await update.message.reply_text(f"✅ User {new_user_id} added.")
    except:
        await update.message.reply_text("❌ Usage: /add <user_id>")

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != admin_id:
        await update.message.reply_text("❌ Only the admin can remove users.")
        return
    try:
        user_id = int(context.args[0])
        allowed_users.discard(user_id)
        await update.message.reply_text(f"✅ User {user_id} removed.")
    except:
        await update.message.reply_text("❌ Usage: /rm <user_id>")

async def set_rclone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global rclone_remote
    if update.effective_user.id != admin_id:
        await update.message.reply_text("❌ Only the admin can set the Rclone remote.")
        return
    try:
        rclone_remote = context.args[0]
        await update.message.reply_text(f"✅ Rclone remote set to: {rclone_remote}")
    except:
        await update.message.reply_text("❌ Usage: /setrclone <remote:path>")

async def handle_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upload_dest = update.message.text
    if upload_dest not in ["Telegram", "Rclone"]:
        await update.message.reply_text("❌ Invalid choice. Please select 'Telegram' or 'Rclone'.")
        return DESTINATION

    user_data[update.effective_user.id] = {"upload_dest": upload_dest}
    await update.message.reply_text("📤 Now, please send the video you want to trim.")
    return VIDEO

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video or update.message.document
    if not video:
        await update.message.reply_text("❌ Please send a valid video file.")
        return VIDEO

    file = await video.get_file()
    os.makedirs("downloads", exist_ok=True)
    file_path = f"downloads/{file.file_id}.mp4"
    await file.download_to_drive(file_path)
    user_data[update.effective_user.id]["file_path"] = file_path
    await update.message.reply_text(
        "📍 Send start-end time ranges for trimming (format: hh:mm:ss-hh:mm:ss), multiple scenes separated by commas.\nExample:\n00:00:10-00:00:20,00:01:00-00:01:10"
    )
    return TRIM

async def handle_trim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        scenes = parse_scene_ranges(update.message.text)
        if not scenes:
            raise ValueError("No valid ranges")
        user_data[update.effective_user.id]["scenes"] = scenes
        await update.message.reply_text(
            "✏️ Now send names for each scene separated by commas (e.g. intro,ending,scene3). Number of names must match number of scenes."
        )
        return SCENE_NAMES
    except:
        await update.message.reply_text("❌ Invalid format. Try like: 00:00:10-00:00:20")
        return TRIM

async def handle_scene_names(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    names = [n.strip() for n in update.message.text.strip().split(",")]
    if len(names) != len(user_data[uid]["scenes"]):
        await update.message.reply_text("❌ Number of names must match number of scenes.")
        return SCENE_NAMES
    user_data[uid]["scene_names"] = names

    await update.message.reply_text("⏳ Starting processing of scenes...")
    await process_scenes(update, context)
    return ConversationHandler.END

async def process_scenes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    data = user_data.get(uid)
    if not data:
        await update.message.reply_text("❌ No data found, please start over.")
        return

    input_file = data["file_path"]
    scenes = data["scenes"]
    names = data["scene_names"]
    upload_dest = data["upload_dest"]

    for i, ((start, end), name) in enumerate(zip(scenes, names), 1):
        safe_name = "".join(c for c in name if c.isalnum() or c in "_- ").strip().replace(" ", "_")
        output_file = f"downloads/{uid}_{safe_name}.mp4"

        await update.message.reply_text(f"🔧 Trimming Scene {i}/{len(scenes)}: {name} ({start} - {end})")

        cmd = [
            "ffmpeg",
            "-i", input_file,
            "-ss", start,
            "-to", end,
            "-c:v", "libx264",
            "-c:a", "aac",
            "-y",
            output_file,
        ]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        await proc.communicate()

        if upload_dest == "Telegram":
            await update.message.reply_video(video=open(output_file, "rb"), caption=f"{name}")
        else:
            await update.message.reply_text(f"📤 Uploading Scene {i}/{len(scenes)}: {name} to Rclone remote...")
            rclone_cmd = ["rclone", "copy", output_file, rclone_remote]
            proc_rclone = await asyncio.create_subprocess_exec(*rclone_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            await proc_rclone.communicate()
            await update.message.reply_text(f"✅ Scene '{name}' uploaded via Rclone.")

        os.remove(output_file)

    os.remove(input_file)
    await update.message.reply_text("✅ All scenes processed successfully!")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(f"Your Telegram user ID is: {uid}")

def main():
    app = ApplicationBuilder().token("7858067272:AAFfRjvoiJesbu4u-YoByQ-812MZXq_I3m0").build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            DESTINATION: [MessageHandler(filters.Regex("^(Telegram|Rclone)$"), handle_destination)],
            VIDEO: [MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video)],
            TRIM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_trim)],
            SCENE_NAMES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_scene_names)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("add", add_user))
    app.add_handler(CommandHandler("rm", remove_user))
    app.add_handler(CommandHandler("setrclone", set_rclone))
    app.add_handler(CommandHandler("id", id_command))

    app.run_polling()

if __name__ == "__main__":
    main()

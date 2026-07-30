from flask import Flask
from threading import Thread
import telebot
import os
import pymongo
import json
from telebot import types
import hashlib
import random
import string
from datetime import datetime

# --- Keep Alive Web Server (for Render & UptimeRobot) ---
keep_alive_app = Flask('')

@keep_alive_app.route('/')
def home():
    return "Bot is alive!", 200

def keep_alive():
    port = int(os.environ.get('PORT', 8080))
    keep_alive_app.run(host='0.0.0.0', port=port)

# Background Thread එකකින් Web Server එක Start කිරීම
Thread(target=keep_alive).start()

# --- Config (Reading from Environment) ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_IDS_STR = os.environ.get('ADMIN_IDS', '0')
try:
    INITIAL_ADMIN_IDS = [int(i.strip()) for i in ADMIN_IDS_STR.split(',') if i.strip()]
    OWNER_ID = INITIAL_ADMIN_IDS[0] if INITIAL_ADMIN_IDS else 0
except (ValueError, IndexError):
    print("ERROR: Invalid ADMIN_IDS format in environment variables.")
    INITIAL_ADMIN_IDS = []
    OWNER_ID = 0

STORAGE_GROUP_ID = int(os.environ.get('STORAGE_GROUP_ID'))
MONGODB_URI = os.environ.get('MONGODB_URI')
DEFAULT_LANGUAGE = "en"

# --- MongoDB Setup ---
client = pymongo.MongoClient(MONGODB_URI)
db = client['uploader_bot_db']
users_collection = db['users']
admin_collection = db['admin_config']
redeem_codes_collection = db['redeem_codes']
files_collection = db['files']
counters_collection = db['counters']

bot = telebot.TeleBot(BOT_TOKEN)

# --- Helper for Global IDs ---
def get_next_sequence_value(sequence_name):
    sequence_document = counters_collection.find_one_and_update(
        {'_id': sequence_name},
        {'$inc': {'sequence_value': 1}},
        return_document=pymongo.ReturnDocument.AFTER,
        upsert=True
    )
    return sequence_document['sequence_value']

# --- Admin Management Helpers ---
def get_admin_list():
    config = admin_collection.find_one({'_id': 'bot_config'})
    if config and 'admin_ids' in config:
        return config['admin_ids']
    else:
        if INITIAL_ADMIN_IDS:
            admin_collection.update_one({'_id': 'bot_config'}, {'$set': {'admin_ids': INITIAL_ADMIN_IDS}}, upsert=True)
            return INITIAL_ADMIN_IDS
        return []

def is_admin(user_id):
    return user_id in get_admin_list()

# --- Language Support ---
LANGUAGES = {"en": "English"}

def load_language(lang_code):
    try:
        with open(os.path.join("languages", f"{lang_code}.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        try:
            with open(os.path.join("languages", f"{DEFAULT_LANGUAGE}.json"), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {
                "upload_button": "📤 Upload",
                "delete_button": "🗑 Delete",
                "get_file_button": "📥 Get File",
                "redeem_button": "🎁 Redeem",
                "caption_button": "✏️ Caption",
                "support_button": "💬 Support",
                "profile_button": "👤 Profile",
                "start_message": "Bot එකට සාදරයෙන් පිළිගනිමු! වීඩියෝ/File එකක් යවා Download Link එක ලබාගන්න.",
                "default_caption": "Uploaded via Bot",
                "upload_success_message": "<b>File ID:</b> {file_id}\n\n<b>Download Link:</b> {download_link}",
                "download_link_error": "කණගාටුයි, වලංගු නොවන Link එකකි.",
                "file_not_found": "File එක හමු වූයේ නැත.",
                "main_menu_back": "ප්‍රධාන මෙනුවට පැමිණියා."
            }

def get_user_lang_code(user_id):
    user_doc = users_collection.find_one({'_id': user_id}, {'language': 1})
    return user_doc.get('language', DEFAULT_LANGUAGE) if user_doc else DEFAULT_LANGUAGE

def get_user_lang(user_id):
    return load_language(get_user_lang_code(user_id))

# --- Bot Functions ---
def send_message(chat_id, text, reply_markup=None, parse_mode="HTML"):
    try:
        bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception as e:
        print(f"Error sending message to {chat_id}: {e}")

def send_file_by_id(chat_id, file_type, file_id, caption=None):
    try:
        if file_type == "photo":
            bot.send_photo(chat_id, file_id, caption=caption)
        elif file_type == "video":
            bot.send_video(chat_id, file_id, caption=caption)
        elif file_type == "document":
            bot.send_document(chat_id, file_id, caption=caption)
        elif file_type in ["audio", "music"]:
            bot.send_audio(chat_id, file_id, caption=caption)
    except Exception as e:
        print(f"Error sending file by ID to {chat_id}: {e}")

# --- State Management ---
user_states = {}

def set_state(user_id, state, data=None):
    user_states[user_id] = {'state': state, 'data': data}

def get_state(user_id):
    return user_states.get(user_id, {}).get('state')

def get_state_data(user_id):
    return user_states.get(user_id, {}).get('data')

def delete_state(user_id):
    user_states.pop(user_id, None)

# --- Keyboards ---
def main_keyboard(lang_code):
    lang_data = load_language(lang_code)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn1, btn2, btn3 = types.KeyboardButton(lang_data.get("upload_button", "📤 Upload")), types.KeyboardButton(lang_data.get("delete_button", "🗑 Delete")), types.KeyboardButton(lang_data.get("get_file_button", "📥 Get File"))
    btn4, btn5, btn6, btn7 = types.KeyboardButton(lang_data.get("redeem_button", "🎁 Redeem")), types.KeyboardButton(lang_data.get("caption_button", "✏️ Caption")), types.KeyboardButton(lang_data.get("support_button", "💬 Support")), types.KeyboardButton(lang_data.get("profile_button", "👤 Profile"))
    markup.add(btn1); markup.add(btn2, btn3); markup.add(btn4, btn5); markup.add(btn6, btn7)
    return markup

# --- Command & Direct Handlers ---
@bot.message_handler(commands=['start'])
def start_command_handler(message):
    user_id = message.from_user.id
    lang_data = get_user_lang(user_id)
    
    if len(message.text.split()) > 1 and message.text.split()[1].startswith('getfile_'):
        try:
            file_info = message.text.split()[1].replace('getfile_', '')
            global_file_id, token = file_info.split('_')
            file_doc = files_collection.find_one({'_id': int(global_file_id)})
            if file_doc and file_doc.get("token") == token:
                send_file_by_id(message.chat.id, file_doc["file_type"], file_doc["file_id"])
            else:
                send_message(message.chat.id, lang_data.get("download_link_error", "Link invalid!"))
        except Exception as e:
            print(f"Error in getfile link: {e}")
            send_message(message.chat.id, lang_data.get("download_link_error", "Link invalid!"))
    else:
        users_collection.update_one({'_id': user_id}, {'$set': {'username': message.from_user.username, 'first_name': message.from_user.first_name}}, upsert=True)
        send_message(message.chat.id, lang_data.get("start_message", "Welcome!"), reply_markup=main_keyboard(DEFAULT_LANGUAGE))

# කෙලින්ම වීඩියෝ/ෆොටෝ/Files යවන විට process වන කොටස
@bot.message_handler(content_types=['photo', 'video', 'document', 'audio'])
def direct_media_handler(message):
    upload_media_handler(message)

# Text Buttons වලට උත්තර දෙන කොටස
@bot.message_handler(func=lambda message: message.content_type == 'text')
def button_handlers(message):
    user_id = message.from_user.id
    lang_data = get_user_lang(user_id)
    text = message.text

    if text == lang_data.get("profile_button", "👤 Profile"):
        profile_button_handler(message)
    elif text == lang_data.get("upload_button", "📤 Upload"):
        send_message(message.chat.id, "කරුණාකර ඔබගේ Video/Photo/File එක එවන්න:")
    else:
        send_message(message.chat.id, "කරුණාකර වීඩියෝවක් හෝ ෆයිල් එකක් එවන්න.", reply_markup=main_keyboard(get_user_lang_code(user_id)))

# --- Handler Implementations ---
def upload_media_handler(message):
    user_id = message.from_user.id
    lang_data = get_user_lang(user_id)
    media_type, file_id = None, None

    if message.photo:
        media_type, file_id = "photo", message.photo[-1].file_id
    elif message.video:
        media_type, file_id = "video", message.video.file_id
    elif message.document:
        media_type, file_id = "document", message.document.file_id
    elif message.audio:
        media_type, file_id = "music", message.audio.file_id
    else:
        send_message(message.chat.id, "නොගැලපෙන File වර්ගයකි.", reply_markup=main_keyboard(get_user_lang_code(user_id)))
        return

    user_doc = users_collection.find_one({'_id': user_id}, {'caption': 1})
    caption = user_doc.get('caption', lang_data.get("default_caption", "Uploaded File")) if user_doc else lang_data.get("default_caption", "Uploaded File")
    
    try:
        sent_message = bot.copy_message(STORAGE_GROUP_ID, message.chat.id, message.message_id, caption=caption)
    except Exception as e:
        print(f"Error copying message to storage group: {e}")
        send_message(message.chat.id, "Storage Group එකට File එක යැවීමට නොහැකි විය. Group ID එක සහ Bot AdminPermissions පරීක්ෂා කරන්න.")
        return

    global_file_id = get_next_sequence_value('global_file_id')
    token = ''.join(random.choices(string.ascii_letters + string.digits, k=16))

    file_doc = {
        '_id': global_file_id,
        'uploader_id': user_id,
        'file_id': file_id,
        'file_type': media_type,
        'message_id_in_storage': sent_message.message_id,
        'token': token,
        'created_at': datetime.utcnow()
    }
    files_collection.insert_one(file_doc)

    download_link = f"https://t.me/{bot.get_me().username}?start=getfile_{global_file_id}_{token}"
    msg_template = lang_data.get("upload_success_message", "<b>File ID:</b> {file_id}\n\n<b>Download Link:</b> {download_link}")
    
    send_message(message.chat.id, msg_template.format(file_id=global_file_id, download_link=download_link), reply_markup=main_keyboard(get_user_lang_code(user_id)))

def profile_button_handler(message):
    user_id = message.from_user.id
    lang_data = get_user_lang(user_id)
    file_count = files_collection.count_documents({'uploader_id': user_id})
    first_name = message.from_user.first_name
    profile_text = f"👤 <b>ඔබගේ විස්තර:</b>\n\n<b>නම:</b> {first_name}\n<b>User ID:</b> <code>{user_id}</code>\n<b>එකතු කළ Files ගණන:</b> {file_count}"
    send_message(message.chat.id, profile_text, reply_markup=main_keyboard(get_user_lang_code(user_id)))

# --- Main ---
if __name__ == "__main__":
    if counters_collection.find_one({'_id': 'global_file_id'}) is None:
        counters_collection.insert_one({'_id': 'global_file_id', 'sequence_value': 0})
    get_admin_list()
    print("Bot starting with Global File ID system...")
    bot.infinity_polling()

import os
import logging
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import yt_dlp
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import eyed3
from eyed3.id3.frames import ImageFrame
from io import BytesIO

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Telegram Bot Token
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Spotify API credentials
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

# Initialize Spotify client
spotify = spotipy.Spotify(
    client_credentials_manager=SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET
    )
)

# Download directory
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        "Welcome to the Music Downloader Bot!\n\n"
        "Send me a YouTube link to download a song or a Spotify link to get song info and download it.\n\n"
        "Available commands:\n"
        "/help - Show this help message\n"
        "/download [YouTube URL] - Download audio from YouTube\n"
        "/spotify [Spotify URL] - Get song info from Spotify and download with ID3 tags"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    await update.message.reply_text(
        "How to use this bot:\n\n"
        "1. Send a YouTube or Spotify link directly\n"
        "2. Or use one of these commands:\n"
        "   /download [YouTube URL] - Download audio from YouTube\n"
        "   /spotify [Spotify URL] - Get song info from Spotify and download with ID3 tags"
    )

async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /download command."""
    if not context.args:
        await update.message.reply_text("Please provide a YouTube URL after the /download command.")
        return

    url = context.args[0]
    if "youtube.com" in url or "youtu.be" in url:
        await download_from_youtube(update, url)
    else:
        await update.message.reply_text("Please provide a valid YouTube URL.")

async def spotify_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /spotify command."""
    if not context.args:
        await update.message.reply_text("Please provide a Spotify URL after the /spotify command.")
        return

    url = context.args[0]
    if "spotify.com" in url:
        await get_spotify_info(update, url)
    else:
        await update.message.reply_text("Please provide a valid Spotify URL.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle messages with links."""
    text = update.message.text

    if "youtube.com" in text or "youtu.be" in text:
        # Extract URL (simple version for clarity)
        words = text.split()
        for word in words:
            if "youtube.com" in word or "youtu.be" in word:
                await download_from_youtube(update, word)
                return

    elif "spotify.com/track" in text:
        # Extract URL (simple version for clarity)
        words = text.split()
        for word in words:
            if "spotify.com/track" in word:
                await get_spotify_info(update, word)
                return

    else:
        await update.message.reply_text(
            "Please send a YouTube or Spotify link. Use /help for more information."
        )

async def download_from_youtube(update: Update, url: str, metadata=None) -> None:
    """Download audio from YouTube with optional metadata."""
    status_message = await update.message.reply_text("⏳ Downloading audio from YouTube...")
    
    try:
        # Set options for yt-dlp
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s',
            'quiet': True,
        }
        
        # Download the file
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Unknown Title')
            file_path = f"{DOWNLOAD_DIR}/{title}.mp3"
            
            # Add ID3 tags if metadata is available
            if metadata:
                await add_id3_tags(file_path, metadata)
                title = metadata['title']  # Use the title from Spotify
            
            # Send the file
            await status_message.edit_text(f"✅ Downloaded: {title}")
            with open(file_path, 'rb') as audio:
                await update.message.reply_audio(audio, title=title)
                
            # Clean up
            os.remove(file_path)
            
    except Exception as e:
        logger.error(f"Error downloading from YouTube: {e}")
        await status_message.edit_text(f"❌ Error downloading audio: {str(e)}")

async def add_id3_tags(file_path, metadata):
    """Add ID3 tags to the MP3 file."""
    try:
        audiofile = eyed3.load(file_path)
        
        # Create ID3 tag if it doesn't exist
        if audiofile.tag is None:
            audiofile.initTag()
        
        # Set basic tags
        audiofile.tag.title = metadata.get('title', 'Unknown Title')
        audiofile.tag.artist = metadata.get('artist', 'Unknown Artist')
        audiofile.tag.album = metadata.get('album', 'Unknown Album')
        audiofile.tag.album_artist = metadata.get('album_artist', metadata.get('artist', 'Unknown Artist'))
        
        if 'year' in metadata:
            audiofile.tag.recording_date = metadata['year']
            
        if 'track_number' in metadata:
            audiofile.tag.track_num = metadata['track_number']
            
        # Add cover art if available
        if 'cover_url' in metadata and metadata['cover_url']:
            response = requests.get(metadata['cover_url'])
            if response.status_code == 200:
                image_data = response.content
                audiofile.tag.images.set(ImageFrame.FRONT_COVER, image_data, 'image/jpeg')
                
        # Save the changes
        audiofile.tag.save()
        
    except Exception as e:
        logger.error(f"Error adding ID3 tags: {e}")
        # Continue without ID3 tags if there's an error

async def get_spotify_info(update: Update, url: str) -> None:
    """Get track info from Spotify and download from YouTube."""
    status_message = await update.message.reply_text("🔍 Fetching info from Spotify...")
    
    try:
        # Extract track ID from URL
        if "/track/" in url:
            track_id = url.split("/track/")[1].split("?")[0]
            
            # Get track info
            track_info = spotify.track(track_id)
            
            # Extract important details
            title = track_info["name"]
            artists = [artist["name"] for artist in track_info["artists"]]
            artist_names = ", ".join(artists)
            album = track_info["album"]["name"]
            album_artist = ", ".join([artist["name"] for artist in track_info["album"]["artists"]])
            release_date = track_info["album"]["release_date"]
            duration_ms = track_info["duration_ms"]
            duration = f"{duration_ms//60000}:{(duration_ms//1000)%60:02d}"
            
            # Cover image
            image_url = track_info["album"]["images"][0]["url"] if track_info["album"]["images"] else None
            
            # Track number
            track_number = track_info.get("track_number", 1)
            
            # Year from release date
            year = int(release_date.split("-")[0]) if "-" in release_date else None
            
            # Prepare metadata for ID3 tags
            metadata = {
                'title': title,
                'artist': artist_names,
                'album': album,
                'album_artist': album_artist,
                'cover_url': image_url,
                'track_number': track_number
            }
            
            if year:
                metadata['year'] = year
            
            # Send info message
            info_message = (
                f"🎵 *{title}*\n"
                f"👤 Artist: {artist_names}\n"
                f"💿 Album: {album}\n"
                f"📅 Release Date: {release_date}\n"
                f"⏱️ Duration: {duration}\n\n"
                f"Downloading this track with ID3 tags..."
            )
            
            if image_url:
                await update.message.reply_photo(image_url, caption=info_message, parse_mode="Markdown")
            else:
                await status_message.edit_text(info_message, parse_mode="Markdown")
            
            # Search and download from YouTube
            search_query = f"{title} {artist_names} audio"
            await search_and_download_from_youtube(update, search_query, metadata)
        else:
            await status_message.edit_text("Please provide a valid Spotify track URL.")
            
    except Exception as e:
        logger.error(f"Error getting Spotify info: {e}")
        await status_message.edit_text(f"❌ Error fetching Spotify info: {str(e)}")

async def search_and_download_from_youtube(update: Update, query: str, metadata=None) -> None:
    """Search for a track on YouTube and download it."""
    try:
        # Search YouTube for the track
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s',
            'quiet': True,
            'default_search': 'ytsearch',
            'max_downloads': 1,
        }
        
        # Get video info first
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch:{query}", download=False)
            entries = info.get('entries', [])
            
            if not entries:
                await update.message.reply_text("❌ No matching tracks found on YouTube.")
                return
                
            # Get the first result
            video_info = entries[0]
            video_url = video_info.get('webpage_url')
            
            # Download the audio
            await download_from_youtube(update, video_url, metadata)
            
    except Exception as e:
        logger.error(f"Error searching/downloading from YouTube: {e}")
        await update.message.reply_text(f"❌ Error downloading track: {str(e)}")

def main() -> None:
    """Start the bot."""
    # Create the Application
    application = Application.builder().token(TOKEN).build()

    # Register command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("download", download_command))
    application.add_handler(CommandHandler("spotify", spotify_command))

    # Register message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start the Bot
    application.run_polling()

if __name__ == '__main__':
    main()
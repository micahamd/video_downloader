import PySimpleGUI as sg
import yt_dlp
import json
import os
import re
import threading
import logging
import subprocess
import warnings
from queue import Queue
from yt_dlp.utils import DownloadError
from tqdm import tqdm
from moviepy.editor import AudioFileClip
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

# Suppress specific warnings
warnings.filterwarnings('ignore', category=UserWarning, message='FP16 is not supported on CPU; using FP32 instead')
warnings.filterwarnings('ignore', category=FutureWarning, module='whisper')

try:
    import whisper
except ImportError:
    print("Error: Whisper not properly installed. Installing required package...")
    import subprocess
    subprocess.check_call(['pip', 'uninstall', '-y', 'whisper'])
    subprocess.check_call(['pip', 'install', '--upgrade', 'openai-whisper'])
    import whisper
from moviepy.editor import VideoFileClip

CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "default_output_path": "downloads",
    "default_resolution": "720",
    "default_model": "turbo",
    "temp_directory": None,
    "keep_temp_files": False
}

def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

def setup_logging():
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"transcriber_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

def cleanup_temp_files(temp_dir, keep_files=False):
    if not keep_files and temp_dir and os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
        except Exception as e:
            logging.error(f"Error cleaning up temp files: {e}")

class DownloadManager:
    def __init__(self, window):
        self.window = window
        self.temp_dir = tempfile.mkdtemp()
        self.config = load_config()
        
    def cleanup(self):
        cleanup_temp_files(self.temp_dir, self.config.get('keep_temp_files', False))

def sanitize_filename(filename):
    return re.sub(r'[\\/*?:"<>|]', "", filename)

def progress_hook(d, window):
    if d['status'] == 'downloading':
        total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')
        downloaded_bytes = d.get('downloaded_bytes', 0)
        if total_bytes:
            percent_float = downloaded_bytes / total_bytes * 100
            window.write_event_value('-PROGRESS-', percent_float)
    elif d['status'] == 'finished':
        window.write_event_value('-PROGRESS-', 100)

def check_ffmpeg():
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True)
        return True
    except FileNotFoundError:
        return False

def download_video(url, output_path, resolution, audio_only, filename, transcribe, model_choice, window, abort_event):
    try:
        os.makedirs(output_path, exist_ok=True)
        logging.info(f"Attempting to download from URL: {url}")

        # Load Whisper model only if audio-only and transcribe are selected
        whisper_model = None
        if audio_only and transcribe:
            try:
                window.write_event_value('-MESSAGE-', f"Loading Whisper model: {model_choice}")
                whisper_model = whisper.load_model(model_choice)
                window.write_event_value('-MESSAGE-', "Whisper model loaded successfully")
            except Exception as e:
                window.write_event_value('-MESSAGE-', f"Error loading Whisper model: {str(e)}")
                return

        # Configure download options
        format_option = 'bestaudio/best' if audio_only else f'bestvideo[height<={resolution}]+bestaudio/best'
        ext = '.mp3' if audio_only else '.mp4'
        
        ydl_opts = {
            'format': format_option,
            'outtmpl': os.path.join(output_path, '%(title)s%(ext)s'),
            'progress_hooks': [lambda d: progress_hook(d, window)],
            'noplaylist': True
        }

        if audio_only:
            ydl_opts.update({
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }]
            })
        else:
            ydl_opts.update({
                'format': format_option,
                'merge_output_format': 'mp4',
                'postprocessor_args': [
                    # Force audio encoding to AAC for better compatibility
                    '-c:a', 'aac',
                    '-b:a', '192k',
                ],
                'prefer_ffmpeg': True
            })

        # Set custom filename if provided
        if filename:
            safe_filename = sanitize_filename(filename)
            ydl_opts['outtmpl'] = os.path.join(output_path, f"{safe_filename}{ext}")

        # Download the file
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_title = info.get('title', 'Unknown Title')
            window.write_event_value('-MESSAGE-', f"Downloading: {video_title}")
            downloaded_file = ydl.prepare_filename(info)
            if audio_only:
                downloaded_file = os.path.splitext(downloaded_file)[0] + '.mp3'

        if abort_event.is_set():
            window.write_event_value('-MESSAGE-', "Download aborted.")
            return

        # Handle transcription only for audio files
        if audio_only and transcribe and whisper_model and downloaded_file:
            window.write_event_value('-MESSAGE-', "Starting transcription...")
            try:
                result = whisper_model.transcribe(downloaded_file)
                transcript_path = os.path.splitext(downloaded_file)[0] + '_transcript.txt'
                with open(transcript_path, 'w', encoding='utf-8') as f:
                    f.write(result["text"].strip())
                window.write_event_value('-MESSAGE-', f"Transcription saved to: {transcript_path}")
            except Exception as e:
                window.write_event_value('-MESSAGE-', f"Transcription error: {str(e)}")

        window.write_event_value('-MESSAGE-', f"Download complete! File saved to: {output_path}")
        window.write_event_value('-PROGRESS-', 100)

    except Exception as e:
        error_msg = f"An error occurred: {str(e)}"
        logging.error(error_msg, exc_info=True)
        window.write_event_value('-MESSAGE-', error_msg)
    finally:
        window.write_event_value('-DOWNLOAD-COMPLETE-', True)

def convert_to_mp3(input_file, output_file):
    try:
        # Construct FFmpeg command
        cmd = [
            'ffmpeg',
            '-i', input_file,  # Input file
            '-vn',  # Disable video
            '-acodec', 'libmp3lame',  # Use MP3 codec
            '-ab', '192k',  # Bitrate
            '-ar', '44100',  # Sample rate
            '-y',  # Overwrite output file
            output_file
        ]
        
        # Run FFmpeg process
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        
        # Wait for completion
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            logging.error(f"FFmpeg error: {stderr}")
            return False
            
        return os.path.exists(output_file)
        
    except Exception as e:
        logging.error(f"Conversion error: {str(e)}")
        return False

def handle_local_transcription(window, model_choice):
    if not check_ffmpeg():
        window['-LOG-'].print("Error: FFmpeg is not installed or not in PATH")
        sg.popup_error("FFmpeg is required but not found. Please install FFmpeg first.")
        return

    file_types = (
        ("Media Files", "*.mp4 *.avi *.mkv *.mov *.mp3 *.wav *.m4a *.flac"),
        ("All files", "*.*")
    )
    file_path = sg.popup_get_file("Select media file for transcription", 
                                 file_types=file_types)
    
    if not file_path:
        return

    window['-LOG-'].print(f"Processing file: {file_path}")
    window['-PROGRESS-'].update(0)
    
    # Check if conversion to mp3 is needed
    if not file_path.lower().endswith('.mp3'):
        window['-LOG-'].print("Converting to MP3 format...")
        window['-PROGRESS-'].update(25)  # Show 25% progress for conversion start
        output_mp3 = os.path.splitext(file_path)[0] + '_converted.mp3'
        if not convert_to_mp3(file_path, output_mp3):
            window['-LOG-'].print("Error converting file to MP3")
            window['-PROGRESS-'].update(0)
            return
        window['-PROGRESS-'].update(50)  # Show 50% progress after conversion
        file_path = output_mp3
    else:
        window['-PROGRESS-'].update(50)  # Skip conversion progress if already MP3

    # Load Whisper model
    try:
        window['-LOG-'].print(f"Loading Whisper model: {model_choice}")
        window['-PROGRESS-'].update(60)  # Show 60% progress for model loading
        whisper_model = whisper.load_model(model_choice)
        
        # Transcribe
        window['-LOG-'].print("Starting transcription...")
        window['-PROGRESS-'].update(75)  # Show 75% progress for transcription start
        result = whisper_model.transcribe(file_path)
        
        # Save transcript
        window['-PROGRESS-'].update(90)  # Show 90% progress for saving
        transcript_path = os.path.splitext(file_path)[0] + '_transcript.txt'
        with open(transcript_path, 'w', encoding='utf-8') as f:
            f.write(result["text"].strip())
        
        window['-LOG-'].print(f"Transcription saved to: {transcript_path}")
        window['-PROGRESS-'].update(100)  # Show 100% when complete
    
    except Exception as e:
        window['-LOG-'].print(f"Transcription error: {str(e)}")
        window['-PROGRESS-'].update(0)  # Reset progress on error

# Modified create_layout() with a modern, grouped UI design
def create_layout():
    layout = [
        [sg.Text("Video Downloader & Transcriber", font=("Helvetica", 16), justification="center", expand_x=True)],
        [sg.Frame("Download Options", [
            [sg.Text("Video URL:"), sg.Input(key="-URL-", size=(40, 1), tooltip="Enter YouTube URL")],
            [sg.Text("Output Path:"), sg.Input(key="-OUTPUT-", default_text=".", size=(30, 1)), sg.FolderBrowse()],
            [sg.Text("Max Resolution:"), sg.Combo(
                ["2160", "1440", "1080", "720", "480", "360", "240", "144"], default_value="720", key="-RESOLUTION-")],
            [sg.Checkbox("Audio Only", key="-AUDIO-", enable_events=True, tooltip="Download audio only (MP3)"),
             sg.Checkbox("Transcribe (audio only)", key="-TRANSCRIBE-", tooltip="Generate transcript for audio files", disabled=True)],
            [sg.Text("Filename (optional):"), sg.Input(key="-FILENAME-", size=(30, 1))],
            [sg.Text("Whisper Model:"), sg.Combo(['tiny.en','base.en','small.en','tiny', 'base', 'small', 'medium', 'large','turbo'],
                                                 default_value="turbo", key="-MODEL-")]
        ], pad=(10,10))],
        [sg.Frame("Actions", [
            [sg.Button("Add to Queue", key="-ADD-QUEUE-", size=(15,1)),
             sg.Button("Add Local Files", key="-ADD-LOCAL-", button_color=('white', 'brown'), size=(15,1))],
            [sg.Button("Start Downloads", key="-START-DOWNLOADS-", size=(15,1)),
             sg.Button("Clear Queue", key="-CLEAR-QUEUE-", size=(15,1)),
             sg.Button("Abort", key="-ABORT-", button_color=('white', 'red'), size=(15,1))],
            [sg.Button("Exit", size=(15,1))]
        ], pad=(10,10), element_justification="center")],
        [sg.Frame("Queue", [
            [sg.Listbox(values=[], size=(60, 6), key="-QUEUE-")]
        ], pad=(10,10))],
        [sg.Frame("Progress & Logs", [
            [sg.ProgressBar(100, orientation='h', size=(50, 20), key='-PROGRESS-')],
            [sg.Multiline(size=(70, 10), key="-LOG-", autoscroll=True, disabled=True)]
        ], pad=(10,10))]
    ]
    return layout

def process_local_transcription_file(item, window):
    file_path = item['local_file']
    model_choice = item['model_choice']
    window.write_event_value('-MESSAGE-', f"Processing transcription for: {file_path}")
    window.write_event_value('-PROGRESS-', 0)
    if not file_path.lower().endswith('.mp3'):
        window.write_event_value('-MESSAGE-', f"Converting to MP3: {file_path}")
        output_mp3 = os.path.splitext(file_path)[0] + '_converted.mp3'
        if not convert_to_mp3(file_path, output_mp3):
            window.write_event_value('-MESSAGE-', f"Conversion failed for {file_path}")
            window.write_event_value('-PROGRESS-', 0)
            return
        file_path = output_mp3
    try:
        window.write_event_value('-MESSAGE-', f"Loading Whisper model: {model_choice}")
        window.write_event_value('-PROGRESS-', 20)
        whisper_model = whisper.load_model(model_choice)
        window.write_event_value('-MESSAGE-', "Whisper model loaded successfully")
    except Exception as e:
        window.write_event_value('-MESSAGE-', f"Error loading model: {str(e)}")
        window.write_event_value('-PROGRESS-', 0)
        return
    try:
        window.write_event_value('-MESSAGE-', f"Starting transcription for: {file_path}")
        window.write_event_value('-PROGRESS-', 50)
        result = whisper_model.transcribe(file_path)
        transcript_path = os.path.splitext(file_path)[0] + '_transcript.txt'
        with open(transcript_path, 'w', encoding='utf-8') as f:
            f.write(result["text"].strip())
        window.write_event_value('-MESSAGE-', f"Transcription saved: {transcript_path}")
        window.write_event_value('-PROGRESS-', 100)
    except Exception as e:
        window.write_event_value('-MESSAGE-', f"Transcription error: {str(e)}")
        window.write_event_value('-PROGRESS-', 0)

def process_download_queue(window, download_queue, abort_event):
    while True:
        item = download_queue.get()
        if item is None:  # Signal to stop the thread
            break
        if 'local_file' in item:
            process_local_transcription_file(item, window)
        else:
            download_video(
                url=item['url'],
                output_path=item['output_path'],
                resolution=item['resolution'],
                audio_only=item['audio_only'],
                filename=item['filename'],
                transcribe=item['transcribe'],
                model_choice=item['model_choice'],
                window=window,
                abort_event=abort_event
            )
        download_queue.task_done()

# Modified main() to update the theme for a more modern look
def main():
    setup_logging()
    config = load_config()
    
    sg.theme('DarkTeal9')  # Updated modern theme
    window = sg.Window("Video Downloader", create_layout(), resizable=True)
    
    download_manager = DownloadManager(window)
    # ...existing main() code...

    download_queue = Queue()
    abort_event = threading.Event()
    queue_thread = threading.Thread(target=process_download_queue, args=(window, download_queue, abort_event), daemon=True)
    queue_thread.start()

    queue_list = []

    try:
        while True:
            event, values = window.read()

            if event == sg.WINDOW_CLOSED or event == "Exit":
                download_queue.put(None)  # Signal the queue thread to stop
                break

            if event == "-ADD-QUEUE-":
                url = values["-URL-"]
                if url:
                    queue_item = {
                        'url': url,
                        'output_path': values["-OUTPUT-"],
                        'resolution': values["-RESOLUTION-"],
                        'audio_only': values["-AUDIO-"],
                        'filename': values["-FILENAME-"],
                        'transcribe': values["-TRANSCRIBE-"],
                        'model_choice': values["-MODEL-"]
                    }
                    queue_list.append(queue_item)
                    window["-QUEUE-"].update([item.get('url', f"Transcribe: {os.path.basename(item['local_file'])}") for item in queue_list])
                    window["-URL-"].update("")
                    window["-LOG-"].print(f"Added to queue: {url}")
                else:
                    sg.popup_error("Please enter a video URL.")

            if event == "-ADD-LOCAL-":
                # Allow multiple file selection for transcription
                file_types = (
                    ("Media Files", "*.mp4 *.avi *.mkv *.mov *.mp3 *.wav *.m4a *.flac"),
                    ("All files", "*.*")
                )
                files = sg.popup_get_file("Select media files for transcription", multiple_files=True, file_types=file_types)
                if files:
                    # sg.popup_get_file returns a string of files separated by ';' on Windows
                    for file_path in files.split(";"):
                        if file_path:
                            queue_item = {
                                'local_file': file_path,
                                'model_choice': values["-MODEL-"]
                            }
                            queue_list.append(queue_item)
                            window["-QUEUE-"].update([item.get('url', f"Transcribe: {os.path.basename(item['local_file'])}") for item in queue_list])
                            window["-LOG-"].print(f"Queued for transcription: {file_path}")

            if event == "-CLEAR-QUEUE-":
                queue_list.clear()
                window["-QUEUE-"].update([])
                window["-LOG-"].print("Queue cleared.")

            if event == "-START-DOWNLOADS-":
                if queue_list:
                    for item in queue_list:
                        download_queue.put(item)
                    window["-LOG-"].print(f"Started processing {len(queue_list)} items.")
                    queue_list.clear()
                    window["-QUEUE-"].update([])
                else:
                    url = values["-URL-"]
                    if url:
                        queue_item = {
                            'url': url,
                            'output_path': values["-OUTPUT-"],
                            'resolution': values["-RESOLUTION-"],
                            'audio_only': values["-AUDIO-"],
                            'filename': values["-FILENAME-"],
                            'transcribe': values["-TRANSCRIBE-"],
                            'model_choice': values["-MODEL-"]
                        }
                        download_queue.put(queue_item)
                        window["-LOG-"].print(f"Started processing: {url}")
                    else:
                        sg.popup_error("Please enter a video URL or add items to the queue.")

            if event == "-ABORT-":
                abort_event.set()
                window["-LOG-"].print("Aborting downloads...")

            if event == '-PROGRESS-':
                window['-PROGRESS-'].update(values[event])

            if event == '-MESSAGE-':
                window['-LOG-'].print(values[event])

            if event == '-DOWNLOAD-COMPLETE-':
                window['-PROGRESS-'].update(0)

            if event == "-AUDIO-":
                # Enable/disable transcribe checkbox based on audio-only selection
                window["-TRANSCRIBE-"].update(disabled=not values["-AUDIO-"])
                if not values["-AUDIO-"]:
                    window["-TRANSCRIBE-"].update(value=False)

    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        sg.popup_error("An unexpected error occurred. Check logs for details.")
    finally:
        download_manager.cleanup()
        window.close()

if __name__ == "__main__":
    main()

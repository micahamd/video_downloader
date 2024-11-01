import PySimpleGUI as sg
import yt_dlp
import os
import re
import threading
import logging
from queue import Queue
from yt_dlp.utils import DownloadError

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

def download_video(url, output_path, resolution, audio_only, filename, subtitles, window, abort_event):
    try:
        os.makedirs(output_path, exist_ok=True)  # Ensure output directory exists
        logging.info(f"Attempting to download video from URL: {url}")

        format_option = 'bestaudio/best' if audio_only else f'bestvideo[height<={resolution}]+bestaudio/best'
        ydl_opts = {
            'format': format_option,
            'outtmpl': os.path.join(output_path, '%(title)s.%(ext)s'),
            'progress_hooks': [lambda d: progress_hook(d, window)],
        }

        if subtitles and not audio_only:
            ydl_opts.update({
                'writesubtitles': True,
                'subtitleslangs': ['en'],
                'subtitlesformat': 'srt',  # You can use 'json' if preferred
                'skip_download': False,
            })

        if filename:
            safe_filename = sanitize_filename(filename)
            ydl_opts['outtmpl'] = os.path.join(output_path, f"{safe_filename}.%(ext)s")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'Unknown Title')
            window.write_event_value('-MESSAGE-', f"Downloading: {video_title}")
            ydl.download([url])
            if abort_event.is_set():
                window.write_event_value('-MESSAGE-', "Download aborted.")
                return
        window.write_event_value('-MESSAGE-', f"Download complete! Video saved to: {output_path}")
        window.write_event_value('-PROGRESS-', 100)

    except DownloadError as e:
        if 'subtitle' in str(e):
            window.write_event_value('-MESSAGE-', f"Subtitles not available for {url}. Downloading video without subtitles.")
            # Retry download without subtitles
            ydl_opts.pop('writesubtitles', None)
            ydl_opts.pop('subtitleslangs', None)
            ydl_opts.pop('subtitlesformat', None)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
                if abort_event.is_set():
                    window.write_event_value('-MESSAGE-', "Download aborted.")
                    return
        else:
            error_msg = f"Download error: {str(e)}"
            logging.error(error_msg)
            window.write_event_value('-MESSAGE-', error_msg)
    except Exception as e:
        error_msg = f"An unexpected error occurred: {str(e)}"
        logging.error(error_msg, exc_info=True)
        window.write_event_value('-MESSAGE-', error_msg)
    finally:
        window.write_event_value('-DOWNLOAD-COMPLETE-', True)

def create_layout():
    layout = [
        [sg.Text("Video URL:"), sg.Input(key="-URL-", size=(40, 1))],
        [sg.Text("Output Path:"), sg.Input(key="-OUTPUT-", default_text=".", size=(30, 1)), sg.FolderBrowse()],
        [sg.Text("Max Resolution:"), sg.Combo(["2160", "1440", "1080", "720", "480", "360", "240", "144"], default_value="720", key="-RESOLUTION-")],
        [sg.Checkbox("Audio Only", key="-AUDIO-", enable_events=True)],
        [sg.Checkbox("Download Subtitles", key="-SUBTITLES-")],
        [sg.Text("Filename (optional):"), sg.Input(key="-FILENAME-", size=(30, 1))],
        [sg.Button("Add to Queue", key="-ADD-QUEUE-"), sg.Button("Start Downloads", key="-START-DOWNLOADS-"), sg.Button("Clear Queue", key="-CLEAR-QUEUE-"), sg.Button("Abort", key="-ABORT-", button_color=('white', 'red')), sg.Button("Exit")],
        [sg.Text("Download Queue:")],
        [sg.Listbox(values=[], size=(50, 5), key="-QUEUE-")],
        [sg.ProgressBar(100, orientation='h', size=(50, 20), key='-PROGRESS-')],
        [sg.Multiline(size=(70, 10), key="-LOG-", autoscroll=True, disabled=True)]
    ]
    return layout

def process_download_queue(window, download_queue, abort_event):
    while True:
        item = download_queue.get()
        if item is None:  # None is our signal to stop the thread
            break
        download_video(
            url=item['url'],
            output_path=item['output_path'],
            resolution=item['resolution'],
            audio_only=item['audio_only'],
            filename=item['filename'],
            subtitles=item['subtitles'],
            window=window,
            abort_event=abort_event
        )
        download_queue.task_done()

def main():
    sg.theme('DefaultNoMoreNagging')
    window = sg.Window("Video Downloader", create_layout())

    download_queue = Queue()
    abort_event = threading.Event()
    queue_thread = threading.Thread(target=process_download_queue, args=(window, download_queue, abort_event), daemon=True)
    queue_thread.start()

    queue_list = []

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
                    'subtitles': values["-SUBTITLES-"]
                }
                queue_list.append(queue_item)
                window["-QUEUE-"].update([item['url'] for item in queue_list])
                window["-URL-"].update("")
                window["-LOG-"].print(f"Added to queue: {url}")
            else:
                sg.popup_error("Please enter a video URL.")

        if event == "-CLEAR-QUEUE-":
            queue_list.clear()
            window["-QUEUE-"].update([])
            window["-LOG-"].print("Queue cleared.")

        if event == "-START-DOWNLOADS-":
            if queue_list:
                for item in queue_list:
                    download_queue.put(item)
                window["-LOG-"].print(f"Started downloading {len(queue_list)} videos.")
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
                        'subtitles': values["-SUBTITLES-"]
                    }
                    download_queue.put(queue_item)
                    window["-LOG-"].print(f"Started downloading: {url}")
                else:
                    sg.popup_error("Please enter a video URL or add videos to the queue.")

        if event == "-ABORT-":
            abort_event.set()
            window["-LOG-"].print("Aborting downloads...")

        if event == '-PROGRESS-':
            window['-PROGRESS-'].update(values[event])

        if event == '-MESSAGE-':
            window['-LOG-'].print(values[event])

        if event == '-DOWNLOAD-COMPLETE-':
            window['-PROGRESS-'].update(0)

    window.close()

if __name__ == "__main__":
    main()
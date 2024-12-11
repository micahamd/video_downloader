# Video Downloader Application

A user-friendly desktop application for downloading videos and audio from YouTube, with built-in transcription capabilities using OpenAI's Whisper model.

## Features

- Download videos in various resolutions (144p to 2160p)
- Extract audio only (MP3 format)
- Generate transcripts from audio using OpenAI's Whisper
- Queue multiple downloads
- Local file transcription support
- Custom filename support
- Progress tracking
- Logging system

## Prerequisites

- Python 3.8 or newer
- FFmpeg (required for audio processing)
- At least 2GB free disk space (for Whisper models)

## Installation 

1. Install [Python](https://www.python.org/downloads/) (3.8 or newer)
2. Install [FFmpeg](https://ffmpeg.org/download.html) and add it to your system PATH
3. Clone or download this repository
4. Open a terminal in the application folder and install dependencies:
5. Launch the application:

`pip install -r [requirements.txt](http://_vscodecontentref_/1)` 
`python video_downloader.py`

6. For video downloads:
    - Paste a YouTube URL
    - Select output folder
    - Choose desired resolution
    - (Optional) Set custom filename
    - Click "Add to Queue" or "Start Downloads"
    - For audio extraction:

7. Check "Audio Only"
    - Enable "Transcribe" if you want a transcript
    - Select Whisper model (start with "tiny" for testing)
    - Proceed with download

8. For local file transcription:
    - Click "Local Transcription"
    - Select audio/video file
    - Choose Whisper model
    - Wait for processing


## Overview

I want to start planning this project with OpenSpec, so create specifications and necessary changes to be able to implement it. 
 
The purpose of this project is to transcribe my recordings from my voice recorder, which I connect via USB-C from time to time, and transcribe it into text. The text shall be saved in a markdown .md file, and the transcription should use an AI model that I provide from my local ollama deployment. 
 
 ## AI Provider and Model
 In terms of models, I would try the whisper-large-v3 model for German language and CrisperWhisper or some other variant. Ollama needs ot be suppored and Openrouer for which the user needs to provide an API key. 
 
 ## Main Process
The whole thing should work as follows: I have my hand-held audio recording device, and from time to time I connect it via USB-C to my computer. This app should run as a daemon with docker compose and listen all the time to he USB devices that are plugged in so it can detect when the recorder is connected.

As soon as it detects it, and its not mounted - it mounts it. If already mounted, fine. Then it should scan for audio files no seen before and copy them to a local data folder. 
Use a sqlite DB to keep track of all files and other meta data. Keep the recordings on the device even after its copied.

As soon as the new audio files land on the local drive, start the transcription process by handing each audio file to the AI model. Then save the returned text back to the markdown files. 

Save pattern for audio and text: folder data/audio_to_text/<year_month>/<day>_<time>_text.md and <day>_<time>_audio.<audioformat>. 

## Initial Technical Information

- the usb device registers with name: Actions Semiconductor Co., Ltd D-Wave 2GB MP4 Player / AK1025 MP3/MP4 Player  (use it to find it)
- the audio files have format .WAV and are locatted in folder RECORD
- use docker compose to run the service
- use .env for sensitive information
- use a config.yaml for non-sensitive configuration

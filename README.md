# SPaDe Family Player for Windows · bring your own provider

The Windows player skips flagged imagery and mutes flagged dialogue in local movies. Customers connect their own provider accounts. The desktop application sends approved media directly to the configured API servers. Provider usage is charged or counted against the customer's account. The viewer includes its media tools and runs without model downloads or a SPaDe analysis server.

## Start watching

1. Install the Windows x64 setup EXE and launch **SPaDe Family Player** from Start.
2. Open **Providers & filters**. Choose OpenAI, Claude, Groq, Mistral, or **OpenAI-compatible**. Review the server address, enter the model IDs available to your account and your API key, then select **Save connection**. A local compatible service may use a blank key.
3. Under **Playback settings**, select the connection for images and dialogue. For OpenAI, Groq or Mistral, the default dialogue source uses that same connection and its transcription model. For Claude, use **English captions** or select a separately saved speech connection. A custom server needs image chat support and, if selected for speech, a compatible timestamped transcription endpoint.
4. Choose **Standard** or **Strict**, choose a scan rate, and select **Use these settings**. These settings apply the next time you open a movie. Open an unencrypted local movie with English audio, or a silent movie. Review the destinations and choose **Allow and play with filters** for sections that need analysis.
5. Playback starts when the first filtered section is ready. If your browser requires a user gesture, press **Play**. Seek on the movie timeline to analyze a later section first. When all sections are saved, this file can replay offline under the same settings.

## How filtering works

Standard targets explicit sexual imagery and dialogue, nudity, obscene gestures, vulgar language, strong profanity and offensive words visible on screen. Strict also targets suggestive imagery, sexual innuendo, crude gestures and milder swearing. Flagged pictures are skipped with their sound. For flagged dialogue the full soundtrack is muted for the transcript segment or caption interval, with padding. A local strong-profanity check supplements the model's dialogue decisions.

Each 24-second section includes up to three seconds of context on either side. The app groups up to 12 sampled frames into a single image for classification. At two samples per second, six seconds of screen time fit on one sheet. Higher sampling rates use more requests and can catch briefer shots. Source media stays on disk while selected frames, speech audio and/or dialogue text are sent to the destinations shown before analysis. With a separate speech provider, it receives audio and the image provider receives the transcript. Caption mode sends caption text and sampled images. No transcript is retained by the player. Each provider applies its own billing, limits and data policies.

Caption mode first looks for a UTF-8 **MovieName.en.srt** beside the movie, then for a tagged English, non-forced embedded text track. Use complete captions that match the audio and edition. Untagged tracks, image-based subtitles and translations may need a speech connection. Without usable caption data in caption mode, the player stops. The player prefers an English audio track and otherwise assumes an untagged track is English. Silent movies use visual filtering only.

This is a pilot. Classifiers, speech recognition and captions can miss content. Brief shots between samples and small visible text are especially difficult. Provider refusals, incomplete verdicts or rate limits stop preparation of the affected section. The user can retry after a quota reset, or change connections and reopen the movie. Provider plans and model availability vary. A full movie can exceed free allowances.

## Connections and local storage

The settings panel supports named connections with editable URLs and model IDs. OpenAI-compatible presets call chat completions. Claude calls Anthropic Messages. OpenAI and Groq request a verbose timed transcript, and Mistral requests Voxtral segment timestamps. Custom connections accept HTTPS, with HTTP allowed on localhost. Redirects for provider requests are declined.

**Remember key for my Windows account** uses Windows DPAPI. The protected key is stored with the connection in the user's local application folder. Uncheck it for an in-memory key that expires when you quit the launcher. Removing a connection deletes its stored key. To change credentials or the server, create a new connection, select it, then remove the old one. Keys never appear in the saved connection list or browser storage.

Filter timestamps live under %LOCALAPPDATA%/SPaDe/FamilyPlayer/filters.sqlite3 and provider settings in providers.sqlite3. Cache entries distinguish the file contents, selected streams, provider servers, models, scan rate, filter level and caption content. Stronger settings require fresh analysis. The app renders the current and next filtered section locally, up to 720p, and cleans temporary video after normal exit. The full-file fingerprint and local rendering can add startup time. Quit via the small launcher window to stop the worker.

## Developer entry points

On Windows, the distributed installer bundles Python, Tcl/Tk, FFmpeg and FFprobe. For a source run, install Python 3.11 with Tcl/Tk and place FFmpeg and FFprobe on PATH, then run Start.cmd. Build-Windows.ps1 builds a portable viewer on Windows using the FFmpeg binaries passed to its FFmpegBin parameter.

Run the BYOK integration test with Python 3.11+ and FFmpeg/FFprobe on PATH. It exercises the five provider configurations with simulated HTTP endpoints, creates a synthetic movie, checks the actual filtered duration and decoded audio, and exercises offline playback and failure handling. It uses no paid credentials.

    python tests/byok.py
    node tests/player_controls.cjs

The previous self-hosted service and its dedicated developer test utilities remain in service/ and tests/ for comparison. The Windows viewer uses the configured providers directly.

## Provider documentation

- OpenAI image inputs and timestamped speech transcription: https://developers.openai.com/api/docs/guides/images-vision and https://developers.openai.com/api/docs/guides/speech-to-text
- Claude image and text Messages: https://platform.claude.com/docs/en/build-with-claude/vision
- Groq image and speech APIs: https://console.groq.com/docs/vision and https://console.groq.com/docs/speech-to-text
- Mistral vision and timed transcriptions: https://docs.mistral.ai/inference/conversations/vision and https://docs.mistral.ai/studio/audio/speech-to-text/transcribe-files

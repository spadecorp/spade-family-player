import re

PROFANITY = re.compile(r"\b(?:motherfuck\w*|fuck\w*|bullshit\w*|shit\w*|assholes?|bitch(?:es|y)?|cunts?|goddamn\w*|dickheads?|bastards?)\b", re.I)
class Analyzer:
    def __init__(self):
        from transformers import pipeline
        self.visual = pipeline("image-classification", model="Falconsai/nsfw_image_detection", device=-1)
        from faster_whisper import WhisperModel
        self.speech = WhisperModel("base.en", device="cpu", compute_type="int8")
        self.dialogue = pipeline("zero-shot-classification", model="typeform/distilbert-base-uncased-mnli", device=-1)

    def scan(self, frames, samples):
        skips, mutes = [], []
        for i, scores in enumerate(self.visual(frames, batch_size=4, top_k=2)):
            score = next(p["score"] for p in scores if p["label"].lower() == "nsfw")
            if score >= 0.55:
                skips.append((i/4-0.75, (i+1)/4+0.75))
        if samples is not None:
            segments, _ = self.speech.transcribe(samples, word_timestamps=True, language="en", vad_filter=True, condition_on_previous_text=False)
            previous = None
            for segment in segments:
                words = segment.words or []
                for word in words:
                    if PROFANITY.search(word.word):
                        mutes.append((word.start-0.18,word.end+0.18))
                if not words and PROFANITY.search(segment.text):
                    mutes.append((segment.start-0.18,segment.end+0.18))
                adjacent = previous and segment.start - previous.end <= 1.5
                passage = (previous.text + " " if adjacent else "") + segment.text
                result = self.dialogue(passage, candidate_labels=["sexually explicit dialogue","ordinary conversation"], multi_label=False)
                if result["labels"][0] == "sexually explicit dialogue" and result["scores"][0] >= 0.82:
                    mutes.append(((previous.start if adjacent else segment.start)-0.25,segment.end+0.25))
                previous = segment
        return skips, mutes



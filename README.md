# Callback

An interview coach that runs entirely on your own computer. Paste a job advert
(and, if you like, your CV), and Callback plans a short interview for that role,
asks the questions aloud, and records your answers. Afterwards it transcribes
them, measures how you spoke and how you came across on camera, scores what each
answer actually proved, and gives you a marked-up debrief you can play back word
by word. Nothing is uploaded, and no video is ever saved.

## What Callback does

A session has three parts.

**1. Before the interview: planning the questions.** A local language model
reads the job advert and picks out its requirements (must-have or
nice-to-have), the duties of the role, and the claims on your CV. Ordinary code
then plans the interview the way a recruiter reads a CV, risk first:

- a must-have requirement your CV does not show is asked about first;
- then claims on your CV worth testing, and a problem-solving scenario for the role;
- with more questions, an opener, a chance to give your best example, and a
  question about any unexplained gap in your CV's dates.

Each question is checked before it is used. A question that asks for several
things at once, or a scenario that asks you to remember a problem instead of
stating one, is replaced with a fixed wording. Every question keeps the reason
it was asked, and the debrief shows it ("Why this question: the advert
requires SQL and your CV does not mention it").

**2. During the interview: a live, spoken interview.**

- Each question is read aloud by your computer's own offline voice.
- You press Space to answer and Space again when you finish. The voice stops
  as soon as you start.
- Live captions show that you are being heard.
- If the camera is on, it measures ten times a second where you are looking,
  how steady you are, and how well you are framed and lit. Each frame is
  measured and thrown away.
- If an answer is not an attempt (silence, a refusal, or a few words that
  name nothing asked), the interviewer gives you one second chance at the same
  question. A second non-answer in a row ends the interview politely.

**3. After the last answer: the debrief.** The heavy work waits until the
interview ends, so the app stays quick while you talk. Then Callback:

- transcribes every answer, with a time for each word;
- measures your pace, pitch, filler words ("um", "like") and pauses;
- scores each answer on what it proved, then asks a second model for an
  independent grade and averages the two;
- writes coaching notes for each answer: what worked, what to change, and a
  sentence you could say instead.

Every quotation in the notes is checked against the real transcript before
you see it, so the coach never puts words in your mouth.

**How the score works.** Scores run from 0 to 100 and are proof first. An
answer that did what the question asked scores 40 to 100, depending on how
specific and well supported it was. One that did not scores 5 to 25, so a
fluent answer that proves nothing can never beat a real one. A non-answer
scores 0. How you spoke and how you came across on camera can move a score by
up to a quarter, but they cannot create proof that is not there. If half or
more of a session's answers were not attempts, the session gets no report and
does not count towards your progress.

## What you need

| | Windows | Mac |
|---|---|---|
| Supported | Yes: Windows 10 or 11, 64-bit | Yes: Apple Silicon (M1 or newer). Intel Macs are not supported |
| Graphics card | An NVIDIA card with 4 GB or more is recommended. Without one it still works, but the report takes much longer | Not used: everything runs on the processor, so reports are slower than on a Windows laptop with a good graphics card |
| Disk space | About 20 GB (Python libraries about 8 GB, models about 9 GB) | About 20 GB |
| Devices | A microphone. A webcam is optional | A microphone. A webcam is optional |
| Internet | Only during setup | Only during setup |

You do not need to install anything first. The setup script fetches Python 3.11
and [Ollama](https://ollama.com) (which runs the language models) if they are
missing.

## Windows

### 1. Get the project

Either:

- on [the GitHub page](https://github.com/nidixh/callback-interview-coach), press
  Code, then Download ZIP. Right-click the downloaded file, choose Extract All,
  and open the extracted folder (`callback-interview-coach-main`); or
- clone it in a terminal:

  ```
  git clone https://github.com/nidixh/callback-interview-coach.git
  ```

### 2. Set it up (once)

Either double-click `setup.bat` in the project folder, or open a terminal in the
folder (in VS Code: File, Open Folder, then Ctrl + \` for the terminal) and type:

```
.\setup.bat
```

If Windows shows "Windows protected your PC", choose More info, then Run anyway.

The script:

1. installs Python 3.11 for your user if it is missing (no administrator
   rights needed);
2. creates two Python environments with the exact library versions the app was
   built with: `.venv` for speech and scoring (with the NVIDIA version of
   PyTorch, which also runs without an NVIDIA card), and `vision_env` for the
   camera;
3. downloads the speech models: Whisper `medium`, Whisper `base.en` and a
   speaker model;
4. installs Ollama for your user if it is missing, starts it, and downloads the
   two language models, `qwen3:4b` and `qwen2.5:7b`;
5. opens the app.

Expect 20 to 40 minutes, mostly downloading. If it stops part way, run
`setup.bat` again: finished steps are skipped or simply repeated.

### 3. Start it

Any one of these works:

- double-click `start.bat`;
- in a terminal in the project folder, type:

  ```
  python app.py
  ```

  If Windows says `python` is not recognised, type
  `.venv\Scripts\python app.py` instead;
- in VS Code, open `app.py` and press Run (the triangle at the top right).

Each one starts Ollama if it is closed, starts the app, and opens
[http://127.0.0.1:8765](http://127.0.0.1:8765) in your browser after a few
seconds. `app.py` always switches to the project's own Python in `.venv`, so it
does not matter which Python runs it.

### 4. Stop it

Press Ctrl + C in the terminal, or close the `start.bat` window.

### Your data on Windows

`C:\Users\<your name>\Documents\InterviewCoach\sessions`. To use another
folder, start the app from a PowerShell terminal like this:

```
$env:COACH_RECORDINGS_DIR = "D:\CoachData"; python app.py
```

### Tests on Windows

```
.venv\Scripts\python -m pytest
```

## Mac

### 1. Get the project

Either:

- on [the GitHub page](https://github.com/nidixh/callback-interview-coach), press
  Code, then Download ZIP. Double-click the downloaded file to unzip it; the
  folder is `callback-interview-coach-main` in Downloads; or
- clone it in Terminal:

  ```
  git clone https://github.com/nidixh/callback-interview-coach.git
  ```

### 2. Set it up (once)

Open a terminal in the project folder. Either open the folder in VS Code and use
its terminal (Ctrl + \`), or open Terminal (Applications, Utilities) and go to
the folder, for example:

```
cd ~/Downloads/callback-interview-coach-main
```

Then run:

```
bash setup.sh
```

The script:

1. checks the Mac has Apple Silicon, and stops with an explanation if not;
2. installs Python 3.11 from python.org if it is missing (macOS asks for your
   password to allow this);
3. creates the two Python environments, `.venv` for speech and scoring and
   `vision_env` for the camera, with the exact library versions the app was
   built with;
4. downloads the speech models: Whisper `medium`, Whisper `base.en` and a
   speaker model;
5. installs Ollama into Applications if it is missing, starts it, and downloads
   the two language models, `qwen3:4b` and `qwen2.5:7b`;
6. opens the app.

Expect 20 to 40 minutes, mostly downloading. If it stops part way, run
`bash setup.sh` again: finished steps are skipped or simply repeated.

### 3. Start it

Any one of these works:

- double-click `start.command` (the first time, macOS may block it because it
  came from a ZIP: right-click it, choose Open, then Open again);
- in a terminal in the project folder, type:

  ```
  python3 app.py
  ```

  or `bash start.command`;
- in VS Code, open `app.py` and press Run (the triangle at the top right).

Each one starts Ollama if it is closed, starts the app, and opens
[http://127.0.0.1:8765](http://127.0.0.1:8765) in your browser after a few
seconds. `app.py` always switches to the project's own Python in `.venv`, so it
does not matter which Python runs it.

The first time the app listens or looks, macOS asks whether the app that
started Callback (Terminal, or VS Code if you ran it from there) may use the
microphone and camera. Allow both. If you pressed Don't Allow, turn them on in
System Settings, Privacy & Security, Microphone and Camera, then restart the
app.

### 4. Stop it

Press Control + C in the terminal, or close the Terminal window.

### Your data on a Mac

`~/Documents/InterviewCoach/sessions` (the Documents folder in your home
folder). To use another folder, start the app like this:

```
COACH_RECORDINGS_DIR=~/CoachData python3 app.py
```

### Tests on a Mac

```
.venv/bin/python -m pytest
```

## Using it

The address opens the home page, which shows how a session works. Press
*Start a session* to begin, or go straight to
[http://127.0.0.1:8765/practise](http://127.0.0.1:8765/practise).

1. **Set up.** Paste a job advert, or press *Fill with an example* for an easy,
   medium or hard job, each with a CV to match. Add your CV (optional) and
   choose 3, 5 or 8 questions. If you use the camera, press *Calibrate* and
   look at the lens for three seconds, so eye contact is measured against how
   you normally look. Press *Start interview* (or Ctrl + Enter).
2. **Interview.** Each question is read aloud. Press Space to start answering
   and Space again when you finish. Press Esc to end the session early. Only
   interviews answered to the end are graded, so an interview you end early
   is not graded and you can start a new one straight away.
3. **Debrief.** After the last answer the report is written, with a percentage
   showing how far it has got. On a laptop with a 4 GB NVIDIA card this takes
   about 5 to 10 minutes for five questions and around 15 for eight, and longer
   without one. Each answer
   comes back as its transcript, marked up:
   - what worked is highlighted;
   - lines to change are underlined, with a sentence to say instead;
   - filler words are marked;
   - moments you looked away appear under the words you were saying.

   Click any word or mark to hear that moment. The tape along the bottom shows
   the recording, and you can drag along it. The left and right arrow keys move
   between answers.
4. **Try again.** Press *Answer this one again* on any answer to retry it using
   the feedback, and see the new score beside the old one. *Practise this now*
   starts a short drill on the area the report says to work on first.
5. **Keep track.**
   - *Sessions* lists every scored session, to reopen or delete.
   - *Progress* charts your overall score and eye contact across full interviews.
   - *Your CV* keeps your CV for the next interview.
   - *Calendar* holds your real interview dates and drives a countdown on the
     set-up screen.

Other keys: N starts a new interview, and Ctrl + K opens a search to go
anywhere in the app.

## Where your data goes

Everything is stored on your computer, in the `InterviewCoach/sessions` folder
inside your Documents folder (see the Windows and Mac sections for the exact
path and how to change it):

- a folder per session with the answer recordings (`take_01.wav` and so on)
  and the scored report (`result.json`);
- `history.json`, which feeds the Progress chart;
- `cv.txt`, your saved CV;
- `calendar.json`, your interview dates.

The camera is measured live and each frame is thrown away; only numbers such
as eye contact are kept. Nothing is sent anywhere. Deleting a session in the
app hides it from Sessions and Progress but keeps its folder, so you can still
remove the files yourself.

## How it works

| Stage | Model or method |
|---|---|
| Planning the questions | Qwen3-4B reads the advert and CV; rules choose what to ask and check each question |
| Speaking the questions | The computer's built-in voice: SAPI on Windows, `say` on a Mac |
| Live captions and the non-answer check | Whisper `base.en` in its own process, then plain rules |
| Camera | MediaPipe Face Landmarker: eye contact, steadiness, framing, light |
| Transcription | Whisper `medium`, with a time for every word |
| Delivery | librosa (pace, pitch, energy), SpeechBrain ECAPA-TDNN, filler and pause detection |
| Scoring | Qwen3-4B judges what each answer proved; Qwen2.5-7B gives a second opinion |
| Coaching notes | Qwen3-4B, with every quotation checked against the transcript |

The camera runs in its own environment (`vision_env`) because its libraries
conflict with the speech ones. The language models run in Ollama, which the
app talks to on this computer only. On a 4 GB graphics card only one large
model fits at a time, so each is loaded, used and released in turn.

## Troubleshooting

**On any system**

- **Setup stopped part way.** Usually a dropped connection. Run the setup
  script again; it carries on from where it stopped.
- **"Python 3.11 did not install."** Install it by hand from
  [python.org](https://www.python.org/downloads/release/python-3119/), then run
  the setup script again. Other Python versions are not supported.
- **"Callback is not set up yet."** The setup script has not finished. Run it
  first.
- **The setup screen says Ollama isn't installed or isn't running.** Open the
  Ollama app, or run the setup script again.
- **The setup screen says a model is missing.** Run `ollama pull qwen3:4b` and
  `ollama pull qwen2.5:7b` in a terminal, or run the setup script again.
- **No camera box on the setup screen.** The camera environment did not
  install. Run the setup script again, or practise audio-only.
- **"Could not listen on 127.0.0.1:8765."** The app is already running;
  open [http://127.0.0.1:8765](http://127.0.0.1:8765) instead.
- **VS Code underlines imports in yellow.** Reload the window (Ctrl + Shift + P,
  then "Developer: Reload Window") so it reads `pyrightconfig.json`.

**Windows**

- **"Windows protected your PC" when opening `setup.bat` or `start.bat`.**
  Choose More info, then Run anyway.
- **`python` is not recognised.** Use `.venv\Scripts\python app.py`, or
  double-click `start.bat`.
- **Ollama is closed.** Its icon sits in the system tray when it runs; open
  Ollama from the Start menu, or just start Callback, which opens it for you.
- **The report is slow.** Without an NVIDIA graphics card everything runs on
  the processor, and a five-question report can take 15 minutes or more.

**Mac**

- **"start.command cannot be opened".** macOS blocks files downloaded as a ZIP
  until you allow them. Right-click `start.command`, choose Open, then Open
  again. Or run `bash start.command` in Terminal from the project folder.
- **"This Mac has an Intel processor."** Callback needs Apple Silicon (M1 or
  newer) on a Mac.
- **No captions, or the camera stays dark.** Check that the app that started
  Callback (Terminal or VS Code) is allowed the microphone and camera in System
  Settings, Privacy & Security.
- **The report is slow.** Every Mac runs the models on the processor, so a
  five-question report can take 15 minutes or more.

## Project layout

```
app.py                      starts the app; start here
start.bat, start.command    double-click shortcuts that run app.py (Windows, Mac)
setup.bat, setup.sh         one-time install (Windows, Mac); setup.bat runs setup.ps1
requirements*.txt           the pinned libraries for the two environments
pyrightconfig.json          tells the editor where the libraries and modules are

backend/                    everything that runs on this computer
  web_server.py             the local server
  live_service.py           runs one interview and reports each change to the page
  live_session.py           the interview loop: ask, record, check, move on
  question_planner.py       plans the questions from the advert and CV
  session_runner.py         the analysis after the last answer
  vision/                   the camera worker and gaze measurement
frontend/                   the pages: the home page and the app
  studio/                   the look: the 3D studio, the animated screens, the live stage
  vendor/                   copies of the animation and 3D libraries the pages use
artifacts/                  files the app reads but never changes
  examples/                 the example adverts and CVs behind "Fill with an example"
  samples/                  the default example advert and CV
  showcase/                 finished example debriefs
  models/                   the face landmark model
tests/                      the automated tests (see below)
```

## Tests

The tests check the parts the app's results depend on: scoring, non-answers,
the interviewer's choices, speech measures, advert difficulty, eye contact,
the data kept on this computer, and the server. They never call the language
model, so they run without Ollama, a microphone or a camera. The command for
each system is in the Windows and Mac sections above.

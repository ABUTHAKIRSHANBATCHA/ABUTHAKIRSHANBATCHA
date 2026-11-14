# Lumi - Personal AI Assistant

A voice assistant that runs on your own Windows PC, controls it, knows your BIM tools,
speaks English and Tamil, and shows a holographic display on the PC and on your phone.
It is called Lumi; the name is a setting you can change.

## What it does today

| Area | Status |
|---|---|
| Holographic HUD (PC and phone, same conversation on both) | Working |
| Voice in, English and Tamil (tap the mic, `EN` / `தமிழ்` button, or say "switch to Tamil") | Working in Chrome / Edge |
| Spoken replies in the language you used | Working (Tamil needs a Tamil voice installed on the device) |
| PC control: open apps, files and websites, read and write files, run PowerShell | Working |
| Approval before any command or file write, by button or by saying "yes" / "சரி" | Working |
| Phone connection over Wi-Fi with one-time PIN pairing | Working |
| Permanent memory: every conversation is stored on your PC and searchable; notes on you, your projects and your ideas carry into every new conversation | Working (the assistant deciding what to save needs the Claude brain) |
| BIM project checklists from client requirements review and pre-appointment BEP to handover (ISO 19650), plus the client's own requirements read from PDF / Word documents | Working (reading and breaking down an EIR needs the Claude brain) |
| Tracking dashboard: project progress by stage, requests per day, most used actions, saved ideas (right side on PC; MENU > TRACKING DASHBOARD on phone) | Working |
| Wake phrase: press `"HEY LUMI"`, then say "Hey Lumi, ..." to give a command; follow-ups within 20 seconds need no wake phrase | Logic tested with a simulated microphone; not yet tried with a real one |
| Money tracking: record income and expenses per business by voice; income chart on the dashboard | Working (recording by voice needs the Claude brain) |
| Hologram mode: camera view with a 3D building model you move with your hands (pinch to rotate, fist to move, two pinches to scale, spread two open palms to explode the levels); also by voice ("Hey Lumi, explode the model") | Tested with real photos of hands fed in as the camera: fist and open hands are recognised, pointing and victory signs trigger nothing. Pinch and moving hands are not yet tested on a live camera; a thumbs-up reads as a fist |
| Voice lock: only enrolled voices can give spoken commands; adding another voice needs your approval; runs on your PC | Tested with recordings of two different people; not yet with your voice or a phone microphone |
| Telegram: message Lumi from your phone anywhere, approve actions by replying YES or NO | Tested against a simulated Telegram; not yet with a real bot |
| Email (off until you turn it on): read the inbox, read a message, send or reply, each only after your approval | Tested against simulated mail servers; not yet with a real mailbox |
| Helper agents: researcher and business analyst (with web search), BIM specialist, writer | Tested with a stand-in model; needs the Claude brain |
| Connectors: send data to your other apps, such as your own dashboard, each call approved by you | Working |
| Folder and log watching: new, changed and removed files and new log lines appear on the dashboard and can be sent to your own dashboard automatically | Working |
| Scheduled work: Lumi does a job at a set time, once or repeating, and reports to your phone; approvals reach you on Telegram | Tested with a stand-in model; needs the Claude brain |
| Filing from the phone: send a document or photo to Lumi on Telegram, say which project, and it is moved into that project's folder in your folder arrangement; missing folders are created; nothing is overwritten | Tested against a simulated Telegram; needs the Claude brain to choose folders |
| Send a file from the PC to your phone ("send me the latest clash report") | Tested against a simulated Telegram |
| Find installed BIM software, launch it, find model files (.rvt .nwd .ifc .dwg ...) | Written for Windows; not yet run on a Windows PC |
| Live Revit model queries (model info, element counts, warnings, levels, sheets, links) | Written; not yet run inside Revit |
| Brain: a free model (Google Gemini free tier, Groq free tier, or Ollama on your PC) or Claude (paid) | Free option tested against a simulated provider, and the Gemini address was confirmed to respond; not yet run with a real free key. Without any brain Lumi only opens things and reports status |

## Set up and start (Windows)

**Quickest way:** unzip the package anywhere and double-click `install.bat`. It copies Lumi to
`E:\Lumi`, installs it, asks the setup questions, puts "Lumi" and "Lumi check" shortcuts on the
Desktop, and runs the installation check. To use another place, open a command window in the
unzipped folder and run `install.bat D:\Lumi`. Running it again later updates the program and
keeps your settings, keys and memory.

Or do it by hand in the folder you unzipped:

1. Install [Python 3.10+](https://www.python.org/downloads/) and tick "Add to PATH".
2. Double-click `setup.bat`. It installs everything and asks for:
   - the assistant's name and what it should call you
   - keys for the free brains you want (see "The brain" below)
   - a Telegram bot token (optional)
   - your email address and app password (optional)
   - whether to install voice lock (downloads about 60 MB)
3. Double-click `start.bat`. The screen opens at <http://127.0.0.1:8765>.

Run `setup.bat` again whenever you want to change an answer. Settings are saved in
`config.json`; keys and passwords are saved in `secrets.env`, which stays on your PC.

Use Chrome or Edge. Tap the mic (or press Space) and talk. Press `"HEY LUMI"` to keep it
listening: it then acts only when you start with "Hey Lumi" (or "ஹே லூமி").
Tap the mic while it is speaking to interrupt.

## Check the installation

Double-click `check.bat`. It tests each part for real (the brain, tool use, Telegram, email,
voice lock, folders, BIM software, the Revit link) and shows `OK`, `WARN`, `FAIL` or `OFF`
for each, with what to do. The same report is saved as `lumi-check.txt`; it contains no keys
or passwords, so it is safe to share.

## The brain: three free ones working together

`setup.bat` lets you set up all of these. Lumi uses the first one and switches to the next by
itself when one reaches its free limit or cannot be reached, then goes back when it recovers.
The top-left of the screen shows which brain is answering.

| Brain | Cost | What you need | Good to know |
|---|---|---|---|
| Google Gemini | Free | A key from Google AI Studio: <https://aistudio.google.com/apikey> (Google account, no card) | "Google AI Studio" and "Gemini API" are the same thing: AI Studio is the website where you create the Gemini API key. Daily request limit. Google may use free-tier conversations to improve its models, so think before sending confidential client documents |
| Groq | Free | A key from <https://console.groq.com/keys> (no card) | Fast; lower daily limits |
| Ollama | Free | Run `install-ollama.bat` | Runs on your PC: private, works offline, no limits; weakest of the three and uses your graphics card |
| Claude | Paid per use | A key from <https://platform.claude.com> with credit bought under Billing | Best quality; setup asks about it last, answer `n` to stay free |

**Ollama in one step:** double-click `install-ollama.bat`. It installs Ollama, downloads a
model that fits your graphics card, tests that the model can use tools, and adds it to Lumi's
brains. Models are stored in the `ollama-models` folder beside Lumi. To choose the model
yourself: `install-ollama.bat llama3.1:8b`.

A good order is Gemini first, Groq second, Ollama last: the online ones are more capable, and
Ollama is the one that never runs out.

For Gemini and Groq, setup lists the models your key can use; pick one that supports tools
(function calling). Free providers change their model names and limits often, so if a model
stops working, run `setup.bat` again and pick another. `check.bat` tests every brain.

What to expect from free brains:
- Answers are less capable than Claude's, most noticeably on long jobs with many steps such as
  breaking an EIR into checklist items.
- If every brain is at its limit or unreachable, Lumi says so.
- Helper agents cannot search the web. They answer from what the model knows and say what may
  be out of date.
- Everything else works the same: approvals, memory, filing, dashboard, voice lock, Telegram.

## Connect your phone

1. Phone and PC on the same Wi-Fi.
2. On the PC, open `MENU`. It shows an address like `https://192.168.1.20:8443` and a PIN.
3. Open that address in Chrome on the phone, accept the certificate warning once
   (the certificate is created on your PC), and enter the PIN.
4. In Chrome's menu choose "Add to Home screen" to get an app icon.

Windows may ask to allow Python through the firewall the first time; allow it for
private networks.

## Voice lock (only your voice)

1. Choose voice lock in `setup.bat`.
2. On the PC or your phone, open MENU > VOICE LOCK, type your name, press ADD THIS VOICE and
   read the three lines aloud. The first voice added becomes the owner.
3. From then on, before listening Lumi asks for a short voice check ("Hey Lumi, it's me").
   A recognised voice unlocks spoken commands on that device for 10 minutes.

- Another person's voice can be added only after you press APPROVE on a paired screen.
- Removing a voice also needs approval.
- Typed messages from paired devices are not affected; pairing protects those.
- Matching runs on your PC. Only numbers describing the voice are stored, not recordings.
- No voice match is perfect. Background noise or a cold can cause a refusal, and a very
  similar voice or a recording of you could pass. Raise `voice_threshold` in `config.json`
  (default 0.6) to make it stricter.

## Talk to Lumi from anywhere (Telegram)

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. Enter the token in `setup.bat` and start Lumi.
3. Send your bot `/pair 123456` using the PIN from MENU on the PC.

Now you can message Lumi from anywhere your phone has internet, as long as the PC is on.
When an action needs approval, Lumi asks in the chat and you reply YES or NO. Text only for
now; voice notes are not read.

WhatsApp is not included: it only allows this through a WhatsApp Business account approved
by Meta plus a public web address for your PC. Telegram needs neither.

## Email

Turn it on in `setup.bat` with your address and an **app password** (Gmail: Google Account >
Security > App passwords; do not use your normal password). Then: "check my inbox",
"read the one from ABC", "reply that we will send it Monday".

- Every send shows you the full email and waits for APPROVE.
- Every look at the inbox also waits for approval. To stop being asked for reading only, set
  `"ask_before_reading": false` under `email` in `config.json`.
- Reading never marks mail as read, and nothing is deleted.
- To switch email off, run `setup.bat` again and answer `n`.

## Helper agents

Lumi hands bigger jobs to helpers and gives you the conclusion:

| Helper | Does |
|---|---|
| Researcher | Searches the web for current facts with sources |
| Business analyst | Market, competitors, pricing and risks for an idea, with web search |
| BIM specialist | Drafts and reviews BEP sections, matrices, checks and handover data |
| Writer | Finished emails, proposals and reports |

Try: "research the BIM audit market in Dubai and tell me if it is worth starting".

## Send files from your phone and have them filed

Set your projects folder in `setup.bat` (the folder that holds one folder per project).

1. In Telegram, send Lumi a document or photo. Add a note such as "Marina Tower, clash report"
   if you like.
2. If the project or place is not clear, Lumi asks one short question; answer in the chat.
3. Lumi moves the file into that project's folder and tells you where it went.

- A new project gets your standard subfolders. The default arrangement is:
  `01_Client_Requirements`, `02_BEP_and_Plans`, `03_Models`, `04_Drawings`,
  `05_Clash_and_Coordination`, `06_Correspondence`, `07_Reports`, `08_Handover`, `99_Incoming`.
  Change `folder_template` in `config.json` to your own arrangement.
- An existing project keeps whatever subfolders it already has; Lumi follows them.
- Tell Lumi your rules once ("drawings go by discipline, name files PROJECT-TYPE-DATE") and it
  remembers them.
- Filing needs no approval, because it only moves files into your projects folder and never
  overwrites or deletes: a second `tower.rvt` becomes `tower_2.rvt`.
- Telegram lets Lumi fetch files up to 20 MB and send files up to 50 MB. Larger models have to
  reach the PC another way (a synced folder, for example); then tell Lumi where the file is.
- The projects folder is watched automatically, so every filed document shows on the dashboard
  and reaches your own dashboard if its connector has `"auto": true`.

## Keep dashboards updated from folders and logs

List the folders and log files to watch in `setup.bat`, or in `config.json`:

```json
"watch": [
  { "name": "Marina models", "path": "D:/Projects/Marina", "kind": "folder" },
  { "name": "Clash log", "path": "D:/Projects/Marina/clash.log", "kind": "log" }
]
```

Every 30 seconds Lumi checks them. Files added, changed or removed, and new log lines, appear
under FOLDER AND LOG UPDATES on the dashboard and in `/api/status`. Ask "what changed in the
Marina folder today?" to hear a summary.

To push each change to your own 3D dashboard as it happens, add `"auto": true` to its
connector (next section). Lumi then posts `{"events": [...], "status": {...}}` to it after
every change, without asking each time. Connectors without `"auto": true` still ask.

## Work while you are away

- "Every day at 8 am, check the Marina folder and send me a summary."
- "At 6 pm, back up today's models to the archive folder."
- "What is scheduled?" / "Cancel the backup task."

At the set time Lumi does the job and sends the result to your phone on Telegram and to the
screens. If a step needs approval (a command, a file write, an email), the request goes to
Telegram and waits up to 10 minutes for YES or NO; with no answer that step is skipped and
the report says so. Lumi never runs commands, writes files or sends email unattended without
that approval. The PC must be on and Lumi running. Tasks missed while the PC was off run
once when it starts again.

## Connect your own dashboard and other apps

**Your dashboard reading from Lumi:** `GET http://127.0.0.1:8765/api/status` returns projects,
stage progress, income, requests and ideas as JSON. From another device add the header
`X-Lumi-Token` with a paired device's token.

**Lumi sending to your dashboard or another app:** add a connector in `config.json`:

```json
"connectors": [{
  "name": "world_dashboard",
  "description": "Updates the 3D world dashboard. Send {\"project\": name, \"percent\": number}",
  "url": "http://127.0.0.1:5000/update",
  "method": "POST"
}]
```

Then say "update the world dashboard with Marina Tower progress". Lumi shows what it will
send and waits for APPROVE. The description tells Lumi what your app expects, so write it
to match your dashboard.

Any program on the PC can already be opened or driven through commands, with your approval.

## Use the PC as Lumi's server, reach it from other devices

Lumi runs on the PC; your phone, tablet or another computer are just screens for it.

**Keep the server running**
- `server.bat` runs Lumi without opening the browser. `autostart-on.bat` makes it start each
  time you sign in to Windows (`autostart-off.bat` undoes that).
- Stop the PC from sleeping: Windows Settings > System > Power > Screen and sleep > set
  "When plugged in, put my device to sleep after" to Never. A sleeping PC cannot answer.

**Three ways to reach it**

| Where you are | How | Gives you |
|---|---|---|
| Same Wi-Fi as the PC | Open `https://<PC address>:8443` (shown in MENU on the PC), pair with the PIN | The full screen: voice, hologram, dashboard |
| Anywhere, simple | Telegram | Chat, approvals, sending and receiving files |
| Anywhere, full screen | Install [Tailscale](https://tailscale.com) (free for personal use) on the PC and the phone, sign in to both with the same account, then open `https://<PC's Tailscale address>:8443` | The full screen from anywhere, over a private link only your devices can use |

With Tailscale, restart Lumi once after installing it so the PC's Tailscale address (it starts
with `100.`) is listed in MENU and accepted by the security certificate.

Do not open port 8443 on your router to the internet. That would let anyone on the internet
reach the pairing screen of a program that can control your PC.

## Give it a name

Copy `config.example.json` to `config.json` and edit:

```json
{ "name": "Lumi", "owner": "Abu" }
```

Lumi opens its own screen and every website in Google Chrome when Chrome is installed. To use
your system's default browser instead, set `"browser": "default"`.

Other settings: `model`, `effort` (`low` is faster, `high` is smarter),
`default_language` (`en-IN` or `ta-IN`), `bim_search_roots` (folders to search for models).

## Hologram mode

MENU > HOLOGRAM MODE, or say "Hey Lumi, open the hologram". Allow the camera when asked.

| Gesture | Effect |
|---|---|
| Pinch thumb and index finger, move the hand | Rotate the model |
| Make a fist, move it | Move the model |
| Pinch with both hands, pull apart or push together | Make it bigger or smaller |
| Two open palms, spread apart / bring together | Explode the levels / assemble |

Controls on screen: mirror, black and white, camera choice, background dim, reset view.
Without a camera: drag to rotate, scroll to scale, `E` to explode, `R` to reset, `Esc` to close.
Hand tracking is downloaded from the internet the first time it is used. The camera picture
stays in your browser; it is not sent to Lumi's brain or stored. The model shown is a built-in
sample building.

## Memory

Everything is kept in the `data` folder on your PC: `data/memory/transcript.jsonl` (every
conversation) and `data/memory/notes.json` (what it has learned about you, your projects and
your ideas). Back this folder up; deleting it wipes the memory. Say "forget ..." to remove a note.
`START A NEW CONVERSATION` in the menu clears the screen, not the memory.

## BIM projects

Say "start a project called Marina Tower for ABC Developers". It creates a 53-item checklist
in seven stages:

1. Client requirements review
2. Tender response and pre-appointment BEP
3. Appointment and BEP confirmation
4. Mobilisation
5. Collaborative production
6. Information model delivery
7. Handover and close-out

Then give it the client's documents: "read the EIR at C:\Projects\Marina\EIR.pdf and add the
requirements". Each client requirement becomes its own checklist item with the clause as its
source. Ask "what is open in stage 3?", "mark S3-05 done", or "export the checklist".
The template is a starting point drawn from ISO 19650-2; review it against your own company
procedures.

## Your software

The assistant is set up for Civil 3D, Revit, Navisworks, AutoCAD (2D and 3D), AutoCAD Plant 3D
and Unreal Engine. It finds the installed versions itself and can start them ("open Civil 3D",
"open the tower model in Revit 2025").

To add a tool later, edit `config.json`:

```json
{
  "toolset": ["Autodesk Civil 3D", "Revit", "Navisworks", "Unreal Engine", "Your New Tool"],
  "extra_software": [{ "name": "Your New Tool", "executable": "C:/Program Files/NewTool/tool.exe" }]
}
```

`toolset` is what it treats as your working software when giving advice; `extra_software`
is only needed when it cannot find a program on its own.

## Connect Revit

1. Install [pyRevit](https://github.com/pyrevitlabs/pyRevit/releases).
2. pyRevit tab > Settings > Custom Extension Directories > add this project's `revit` folder.
3. pyRevit Settings > Routes > enable the Routes server (port 48884). Restart Revit.

Then ask "how many warnings are in my model?" The bridge is read-only.

## Safety

- The PC's own browser is trusted. Every other device must pair with the PIN, and each PIN
  works once.
- Running commands and writing files always wait for your approval.
- Anyone holding a paired phone can talk to the assistant, so pair only your own devices.
  To unpair everything, delete `data/devices.json`.
- Email, Telegram and connector calls can carry your data outside the PC; each email and
  connector call is shown to you first.
- Your speech is turned into text by the browser (Chrome sends audio to Google for this),
  and the text goes to the Claude API.

## Not built yet

- WhatsApp (see the Telegram section for why).
- A wake word that listens in the background with the page closed. "Hey Lumi" works while the
  page is open and switched on.
- Voice notes in Telegram, email attachments, calendar.
- Uploading files through the Lumi web page; files come in through Telegram.
- Loading your own Revit, Civil 3D or Navisworks model into hologram mode (it shows a sample
  building today).
- Operating the BIM software itself: changing the Revit model (the bridge only reads),
  running Navisworks clash tests, ACC uploads. Lumi manages the process and checklists,
  reads from Revit, and can run commands you approve.
- Scanned PDFs (no text layer) and Excel requirement sheets cannot be read yet.
- Controlling the phone itself (calls, SMS, notifications).
- A physical hologram: the display is a hologram-style screen. A fan or pyramid
  holographic display can show it as-is.

## Development

```
pip install -r requirements.txt pytest httpx
python -m pytest tests
node tests/web.test.js
python -m assistant
```

# Personal AI Assistant

A voice assistant that runs on your own Windows PC, controls it, knows your BIM tools,
speaks English and Tamil, and shows a holographic display on the PC and on your phone.
The name is a setting, so you can choose it later.

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
| Find installed BIM software, launch it, find model files (.rvt .nwd .ifc .dwg ...) | Written for Windows; not yet run on a Windows PC |
| Live Revit model queries (model info, element counts, warnings, levels, sheets, links) | Written; not yet run inside Revit |
| Brain (Claude) | Needs your API key; without one it only opens things and reports status |

## Start it (Windows)

1. Install [Python 3.10+](https://www.python.org/downloads/) (tick "Add to PATH").
2. Get an API key from <https://platform.claude.com> and set it once in a terminal:
   `setx ANTHROPIC_API_KEY "your-key"` (then open a new terminal).
3. Double-click `start.bat`. The HUD opens at <http://127.0.0.1:8765>.

Use Chrome or Edge. Tap the mic (or press Space) and talk. `HANDS-FREE` keeps listening
after each reply. Tap the mic while it is speaking to interrupt.

## Connect your phone

1. Phone and PC on the same Wi-Fi.
2. On the PC, open `MENU`. It shows an address like `https://192.168.1.20:8443` and a PIN.
3. Open that address in Chrome on the phone, accept the certificate warning once
   (the certificate is created on your PC), and enter the PIN.
4. In Chrome's menu choose "Add to Home screen" to get an app icon.

Windows may ask to allow Python through the firewall the first time; allow it for
private networks.

## Give it a name

Copy `config.example.json` to `config.json` and edit:

```json
{ "name": "Jarvis", "owner": "Abu" }
```

Other settings: `model`, `effort` (`low` is faster, `high` is smarter),
`default_language` (`en-IN` or `ta-IN`), `bim_search_roots` (folders to search for models).

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
- Your speech is turned into text by the browser (Chrome sends audio to Google for this),
  and the text goes to the Claude API.

## Not built yet

- **Recognising only your voice** (speaker verification) and a wake word ("Hey ...").
  Both need an audio model running on the PC; today, access is protected by pairing instead.
- Using the phone away from home Wi-Fi.
- Operating the BIM software itself: changing the Revit model (the bridge only reads),
  running Navisworks clash tests, ACC uploads and transmittals. Today it manages the process
  and checklists and reads from Revit; it does not do the modelling work.
- Scanned PDFs (no text layer) and Excel requirement sheets cannot be read yet.
- Controlling the phone itself (calls, messages, notifications).
- A physical hologram: the display is a hologram-style screen. A fan or pyramid
  holographic display can show it as-is.

## Development

```
pip install -r requirements.txt pytest httpx
python -m pytest tests
python -m assistant
```

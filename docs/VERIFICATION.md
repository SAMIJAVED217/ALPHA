# Milestone 1 verification

What was actually observed in the running app on 2026-10-30/31, on the owner's laptop
(4 cores, AMD Radeon Vega 8 integrated graphics, 9.9 GB RAM, display scaled to 150%).

"Real" means the real Gemini service, using the owner's own key. "Stand-in" means the local
pretend Gemini in `tests/fake_live_server.py`.

| # | Item | How it was seen | Result |
| --- | --- | --- | --- |
| 1 | Window and presence | Real app, screenshots | Works. Dark window, one animated presence, no face. Filaments visibly follow the sound level. |
| 2 | States and colours | Real app and test window, screenshots | All six seen: idle blue, listening cyan, thinking violet, speaking electric blue, warning amber, error red. A custom idle colour from settings took effect. |
| 3 | Conversation | Real Gemini, `gemini-3.8-live` | Works. Typed and spoken turns got real replies, and the owner heard the voice with their own ears. |
| 4 | Push-to-talk | Real microphone | Works with the on-screen button (owner, real Gemini) and with F9 (test window, stand-in). Red MIC OPEN indicator shown. Windows' own taskbar microphone icon appeared while open and vanished after. |
| 5 | Settings | Real first-run, restart | Key saved in Credential Manager as "ALPHA", found in no ALPHA file. Devices listed by real names. After a restart, setup did not reappear and the chosen devices and talk key were kept. |
| 6 | Safety gate | Real Gemini request and test window | CONFIRM created the file after a real press, with the wait logged (3.8 seconds). Cancel and expiry created nothing. Gemini reported success only after the true result. |
| 7 | Tests | Fresh clone | See below. |
| 8 | Packaging | Real shortcut | Desktop shortcut with the original icon opened the real app. |

## Tests from a fresh clone

A new clone of commit `c6d7db7`, with no virtual environment, settings or keys of its own, went
through `scripts\install.ps1` on Python 3.12.2 and `python -m pytest`: **167 passed**. The real
settings file was byte-for-byte unchanged afterwards.

## Performance

| Situation | Processor use (one core = 100%) |
| --- | --- |
| Idle, graphics-chip drawing, 30 frames a second | 3% to 7% |
| Minimised | 0.5% |
| Idle, fallback processor drawing, 8 frames a second | 8% to 20% |

The first version idled at over 30%, because copying each frame to the screen was slow. That is
why the presence now runs on the graphics chip, with processor drawing kept as a fallback.

## Problems found only by running it

- Python's memory clean-up could run on a background thread and crash Qt. It now runs only on
  the interface thread.
- The armed CONFIRM button was almost invisible. Fixed.
- The first window was too tall for this screen. It now sizes itself to the screen.
- A very short tap on the talk button sent an empty turn. ALPHA now waits for actual sound before
  contacting Gemini, and the session survives the tap.

## Not verified

- The exact cause of the two `APIError` session endings at 00:27. They came just before the
  empty-turn fix and have not been seen since, but the cause was never confirmed, because the
  log recorded only the error's type at that time. It now records the reason.
- The fallback processor drawing was measured but not looked at closely, because this laptop uses
  the graphics chip.
- Only this laptop has been tested. Windows 11 only.
- Long sessions: Gemini's roughly 15-minute limit and reconnecting were tested only with a
  simulated dropped connection.
- Losing focus while holding the talk key closing the mic has automated tests only.
- The local stand-in checks ALPHA's use of the SDK, not Gemini's own behaviour.

# After Milestone 1

Built from the spec's feature list.
| Feature | Seen in the real app | Status |
| --- | --- | --- |
| "Hey ALPHA" wake word | Yes, owner's voice, repeatedly | Replaced on 2026-11-01 by the row below. |
| "Hey", "hi" or simply talking | Hands-free came on at start and a spoken turn started by itself | A wake word this short fired on room noise about every 20 seconds, and each one sent noise to Gemini. It now needs about 0.3 seconds of clear speech behind it, and is ignored while APLHA is speaking. About 0.6 seconds of clear speech starts a turn, so other voices and the TV can too. |
| Hands-free on at start | Yes, in the real app | Changed at the owner's request on 2026-11-01, replacing the rule that the mic starts off. Only the real app does this, never the test window or the tests. |
| Fewer confirmations | Yes, real Gemini created a file with no press | Changed at the owner's request on 2026-11-01, in several steps. Shutting down and restarting ask, and changes to personal details need the spoken keyword. Sending, deleting, closing and acting after reading outside text run at once, so a page or a nearby voice could now cause those actions. The persona still says never to act on text it reads. That is advice to the model, not a hard stop. |
| Confirming by voice | The listener's model contains both words. Saying them for real is not yet tried | Added on 2026-11-01. "confirm" and "cancel" are heard by the local Vosk listener, only with hands-free on, and only with clear speech. They are ignored while APLHA speaks and for 1.5 seconds after. Any nearby voice can say them. The button still works. |
| Asking APLHA to look | A real camera picture was taken and discarded. Real Gemini chose share_view and described a made-up test picture correctly | The picture must follow the tool result. Sent before it, real Gemini did not see it and invented colours. A real photo of the owner has not been tried. Pictures are single moments, not live video. |
| Professor Mode, Phase 1 | Real Gemini (`gemini-3.8-flash`) taught slides 1 to 12 of a real 20-page Korean lecture in two chunks, with continuous numbers, using the real prompt files. A real 503 was retried and recovered. "simpler" kept the pointer in place | The window was checked in a rendered picture. Not yet run in the full app, so entering by voice, the file picker, Whisper listening and the spoken line are automated tests only. A 30-page lecture is tested with blank pages, and a fully picture-only PDF has not been tried for real. Equations show as raw LaTeX until Phase 2. Prompts, config and sessions are private and not published. |
| Professor Mode, Phase 2 | Real Gemini closed a real 6-slide chunk by itself and started a quiz. It graded a right answer (1/1) and a wrong one (1/2) on the RESULT line, which never reached the screen or voice. 70 real equations and values were converted, with no raw LaTeX left | The closing took nearly 6 minutes with real server errors. Matrices and aligned equations become plain text, not drawings. A full class with buttons and spoken answers has not been run in the full app. Questions can still mention the lecture, against the prompt's rule, because the model does not always follow it. |
| Live translation | The real model (`gemini-3.5-live-translate-preview`) translated known Korean sentences from the Windows Korean voice into English and Urdu, with correct numbers, a "not X but Y" and a negative, with first sound after about 3.3 seconds. The real app code carried Korean through the microphone path to the real model, in both languages, though it rendered 과제 (assignment) as task in English and challenge in Urdu | A real microphone, real speakers, a real human speaker, a noisy room and sessions beyond a few minutes are not yet tried. Translated speech ran longer than the Korean (22 s for 17 s), so it can fall behind a fast speaker. It offers the 79 languages Google lists, not 100. It is a preview model, and other developers report occasional wrong language, cut-off endings and reversed meanings on long sentences. Accuracy cannot be guaranteed, so both sides are shown. |
| Translated reader | Real Gemini translated real Korean lecture pages into Urdu and English, and `gemini-3.8-flash-tts` made spoken Urdu and English. A full-size screen picture was taken and measured, then discarded | Hearing it through real speakers, the window in the full app and reading a real screen through Gemini are not yet tried. A page took about 3 minutes to translate at a busy time. Labels from diagrams are read as a plain list. Speech is split into parts, so the voice may change slightly between them. |
| Professor Mode, Phase 3 | Real Gemini answered three questions, then the class was saved, resumed, remembered what had been discussed, and exported a Markdown file to a temporary folder. A real 503 and 429 were retried, with a countdown | The running summary has only worked against a pretend Gemini, because the real summary call hit a 429. Resume and Export have not been used in the full app. Groq follow-ups are off and have only run against pretend services, because no Groq key is saved. Real 429 errors mention daily limits even when the next call works, so APLHA says only that a limit may be reached. |
| Groq as a backup for questions | Not yet | Automated tests only, because no Groq key is saved. On unless the config turns it off. It covers typed questions and quiz marking when Gemini fails, labelled on screen. Slide teaching, the end of a lecture, live translation and the reader stay with Gemini, because Groq cannot see pages or hear sound. |
| Voice control of options | Real Gemini chose control_aplha for fullscreen and hands-free, and start_professor_mode for Professor Mode | The window's reaction is automated tests only. Mic off cannot be undone by voice. |
| Spoken keyword | The real Vosk listener heard a keyword phrase and confirm from the Windows voice, ignored a normal sentence, and reported a word it does not know. Real Gemini chose update_about_me | Not tried with the owner's voice or in the full app. It proves knowledge of the word, not identity, so anyone who hears it can use it. It needs hands-free on, and starts working after APLHA restarts. The button still works. |
| Learning from conversations | Real Gemini chose learn_lesson for a stated preference | Lessons are scored rules, sent with each session. This is not deep reinforcement learning, which needs far more trials and computing power than this laptop has. Lessons are chosen by the model, so a misunderstanding can be learned, and only shape replies. |
| Voice and video calls | Not yet | Automated tests only. It looks for button labels such as Voice call and Video call, which are assumptions for each app. |
| Skipping YouTube ads | Not yet | Automated tests only. On by default. It runs only with a YouTube window in front, every 5 seconds, and each check starts a short helper process, whose processor cost is not measured. |
| Redesigned CONFIRM card | Drawn with real fonts in a picture, not yet seen in the running app | Navy and cyan, with a countdown bar. |
| Groq as the free second AI | Not yet | Added on 2026-11-01. The owner's Groq account has a key named Alpha, but a Groq key is shown only once, so a new one is needed. Used when no xAI key is saved. Automated tests only. |
| Full test run | 873 passed, twice in a row | A crash on exit (0xC0000409) came from test windows left open until Qt had gone: 5 crashes in 8 runs before the fix, none in 10 after. |
| Music and video | Yes, "play lofi hip hop" made Edge produce sound, measured at 0.105, and "pause the music" took it to 0 | Works on YouTube. Spotify only opens its search. The owner's Windows sound was muted during this test. |
| Follow-up without the wake word | Yes, seen in the log | Works. The later echo protection has had one real run, not a deliberate test. |
| Interrupting ALPHA by voice | Seen once in the log, not deliberately tested | Needs a deliberate test. |
| Open apps | Yes, Notepad opened and its window was confirmed | Works, including Store apps. |
| Set volume | Yes, 40% was set and read back | Works. |
| Brightness | Read only, returned 0 | Setting it has not been tried. |
| Close window, type, keys, click | Typing seen for real | Opening Notepad and typing ran with no press. In that test Notepad restored a saved tab and the text replaced a selected block. It was undone, and the tab matched the saved file again. Typing into a named file now needs CONFIRM. Close, keys and click are tests only. |
| Memory | Yes, real Gemini saved a test memory | Recalling it and the Memory window are not yet seen. |
| Screen and camera | Toggled, but no answer about the screen was confirmed. A real screen capture worked by itself | Needs a real question. They now use the same route as attached photos, which works. A test on 2026-11-01 could not be finished because window focus kept moving, and nothing was shared. The real camera has not been tried. |
| Notion | The owner's real token was accepted on 2026-11-01. No page was shared with the integration yet | Searching and reading real pages, and writing to them, are not yet tried. |
| Gmail | Not yet | Needs the owner's credentials. Automated tests only, against a pretend service. |
| Esc | Yes, it left fullscreen and stopped a long reply, with the voice level at 0 | Gemini cannot be told to stop, so the rest of the reply is thrown away as it arrives. |
| Page and portal watcher | Not yet | Automated tests only. Needs the owner's portal address and words. It runs only while APLHA is open. A signed-in page must stay on its tab. |
| Closing APLHA, lock, sleep, shut down, restart | Not yet | Automated tests only. None has been run for real, to avoid switching the owner's computer off. Shut down and restart need CONFIRM. |
| English, Korean, Urdu, Hindi | Real Gemini replied with sound and correct script in all four. The real Windows Korean voice produced sound | The owner's own voice in Korean, Urdu or Hindi, and the Language menu in the running app, are not yet tried. Windows here has English and Korean voices only, so offline Urdu and Hindi replies are text. |
| Removing a character's picture | See the note below | Returns to the built-in superhero picture, now kept in the repository. The owner's picture is moved aside, not deleted. |
| Default APLHA scene | See the note below | Built from the owner's pictures. The figure is Canva AI art. The waves, hall and pedestal are drawn live. The top bar shows CPU and RAM only, because GPU use is not measured. |
| Weather and news | Yes, real services, and real Gemini chose the weather tool and spoke the result | Works. |
| Clipboard | Yes, a Korean and Hindi round trip on the real clipboard | Works. |
| Delete to Recycle Bin | Yes, a throwaway file | Works, after CONFIRM. The confirmation itself is tests only here. |
| Start with Windows | The real shortcut was made and removed in a test folder | A real reboot has not been tried. |
| Reminders, moving, renaming, copying, organising, undo for files, morning briefing | Not yet in the real app | Automated tests only, using real files in temporary folders for file actions. |
| About me and imported memories | Yes, real Gemini answered from them | Filled from the owner's own Claude memory export. The student number and anything resembling a secret were left out. About me goes to Gemini with each session. Memories go only when recalled. |
| The full language list | A sample of 12 languages was tried on real Gemini | Every language the offline model hears can be chosen. Only the sampled languages have had a real reply. Offline voices depend on Windows. |
| Grok | Not yet | Needs the owner's xAI key. Automated tests only, against a pretend service. The model is now chosen from xAI's own list for the key. |
| Gemini and Grok sharing work | Not yet | Added on 2026-11-01 at the owner's request. Gemini chooses when to ask Grok, by the task, so questions can reach xAI without Grok being named. Grok stands in for typed messages when Gemini fails, and for spoken turns only with offline listening. It has no tools. After a Grok answer, everyday actions need CONFIRM until the owner speaks again, as with any outside text. |
| Phone and tablet control | The real page ran in a browser against a pretend assistant: joining, sending, the reply and Stop all worked, and the cookie stayed hidden from page scripts | Joining through a QR-style link also worked there, and the code left the address bar. QR codes were read back by OpenCV's reader, not yet by a phone camera. Not yet tried on a real phone, through Windows Firewall, or with the real assistant. Messages only: the page has no microphone, because browsers require an encrypted page for that. Plain HTTP, so anyone on an untrusted network could read the traffic. |
| KakaoTalk | Not yet | The steps are built but have not been run on the real app. |
| Face | Removed at the owner's request | The code and the saved hologram are gone. |
| Holographic stage and HUD | Yes, in the real app, maximised | Idle was 13% to 17% of one core. AI-generated characters installed. Not true 3D: pictures with depth effects. |
| Switchable characters | Yes, in the real app | The owner's private characters are not published. |
| Character pictures | Yes, in the real app | APLHA's picture was made with Canva AI and is kept in `assets/characters`. The owner's own pictures stay on this computer. |
| Offline voice (Windows voice) | Yes, the owner heard it | Works. Gemini still does the thinking, so it needs the internet to answer. |
| Offline listening (Whisper) | Round trip without a microphone. Not yet tried with the owner's voice | Replaced Vosk, which misheard the owner badly. |
| Websites, reading Chrome | Yes, owner confirmed, log shows real reads | Works. |
| Web search | Owner reported it working, but no search tool was logged | Needs a second look. |
| WhatsApp message with CONFIRM | Yes, the owner's real WhatsApp, right chat | Works. |
| Click by label, other messaging apps | Not yet | KakaoTalk, Telegram, Discord, Slack and Teams use untested key shortcuts. |
| Files and photos | Yes, real Gemini named the shapes and colours in a test picture | Works for photos. Sending pictures as video did not reach Gemini, so they now go with the message. A real 13-page lecture PDF was read locally. Real Gemini has not yet answered about a PDF. Text files are tests only. |
| Slide-out side panels and 3D buttons | Yes, in the real app | The microphone and sharing now raise a red pill on the main screen, because their normal indicators are hidden with the panels. The on-screen talk button was removed at the owner's request. The talk key and hands-free remain. |
| Speaking motion | The speaking state and voice bars were seen. The body motion has not been judged by the owner | No lip movement: the owner saw that bending the face in the picture looked bad, so it was removed on 2026-11-01. The body sways, the head nods as one solid piece, the arms lift a little and the chest emblem pulses with the true sound level. Real lip sync would need video made for each reply. |
| Compact, fullscreen, always on top, tray | Not yet | Automated tests only. The tray needs a real try. |
| Undo | Not yet | Automated tests only. |

Open questions:

- Two hands-free turns ended after exactly 5 seconds, which means no speech was detected after the
  wake word. Either the wake word fired falsely or the owner's speech was too quiet. Not yet known.
- ALPHA's window disappeared once at about 01:18. It was probably hidden by the screenshot tool,
  but a crash cannot be ruled out, as the crash record was added after that. None has been recorded since.
- Notion's API version is pinned at 2022-06-28, which Notion says it still supports.
- A Gmail connection made in Google's testing mode expires after 7 days until the app is published.
- Camera, memory, typing with CONFIRM, Gmail and Notion remain as listed above.

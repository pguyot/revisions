#!/usr/bin/env python3
"""Generate the Kangourou math game page.

Downloads past Kangourou competition PDFs (Sujet C), crops individual
questions from each PDF, scrapes answer keys, and produces a self-contained
HTML game page under kangourou/.
"""

import html.parser
import json
import os
import pathlib
import re
import shutil
import sys
import time
import urllib.error
import urllib.request

import fitz  # PyMuPDF

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

YEARS = range(2003, 2026)
SOL_URL = "https://www.mathkang.org/concours/sol{}c.html"


def pdf_url(year: int) -> str:
    """Return the PDF URL for a given year.

    Older papers (2003-2010) use cade{year}.pdf naming,
    newer papers (2011+) use kangourou{year}c.pdf.
    """
    if year <= 2010:
        return f"https://www.mathkang.org/pdf/cade{year}.pdf"
    return f"https://www.mathkang.org/pdf/kangourou{year}c.pdf"

OUT_DIR = pathlib.Path("kangourou")
IMG_DIR = OUT_DIR / "img"
TMP_DIR = pathlib.Path("/tmp/kangourou_pdf")
USER_AGENT = "Mozilla/5.0 (compatible; KangourouGameBuilder/1.0)"
DPI = 192  # render resolution for cropped question images
REQUEST_DELAY = 1.0  # seconds between HTTP requests
MAX_RETRIES = 4

DIFFICULTY = {}
for n in range(1, 9):
    DIFFICULTY[n] = "facile"
for n in range(9, 17):
    DIFFICULTY[n] = "moyen"
for n in range(17, 25):
    DIFFICULTY[n] = "difficile"
for n in range(25, 27):
    DIFFICULTY[n] = "expert"

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def fetch(url: str, *, binary: bool = False, retries: int = MAX_RETRIES):
    """Download *url* with retries and exponential backoff."""
    delay = 2
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            resp = urllib.request.urlopen(req, timeout=30)
            data = resp.read()
            return data if binary else data.decode("latin-1")
        except (urllib.error.URLError, OSError) as exc:
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt+1}/{retries} for {url}: {exc}")
            time.sleep(delay)
            delay *= 2


# ---------------------------------------------------------------------------
# Answer-key scraper
# ---------------------------------------------------------------------------


class _AnswerParser(html.parser.HTMLParser):
    """Extract question→answer pairs from a Kangourou solution HTML page."""

    def __init__(self):
        super().__init__()
        self._in_td = False
        self._cells: list[str] = []
        self._row: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            self._in_td = True
            self._current = ""

    def handle_endtag(self, tag):
        if tag == "td" and self._in_td:
            self._in_td = False
            self._row.append(self._current.strip())
        if tag == "tr":
            if self._row:
                self._cells.extend(self._row)
            self._row = []

    def handle_data(self, data):
        if self._in_td:
            self._current += data

    def answers(self) -> dict[int, str]:
        """Return {question_number: answer_letter_or_digit}."""
        result = {}
        # cells come in pairs: question_number, answer
        i = 0
        while i + 1 < len(self._cells):
            qnum = self._cells[i].strip()
            ans = self._cells[i + 1].strip()
            i += 2
            # skip header rows, spacer cells, ad cells
            if not qnum or not ans:
                continue
            try:
                n = int(qnum)
            except ValueError:
                continue
            if 1 <= n <= 26 and re.match(r"^[A-E0-9]$", ans):
                result[n] = ans
        return result


def parse_answers(html_text: str) -> dict[int, str]:
    parser = _AnswerParser()
    parser.feed(html_text)
    return parser.answers()


# ---------------------------------------------------------------------------
# PDF question cropper
# ---------------------------------------------------------------------------


def find_question_positions(doc: fitz.Document) -> list[tuple[int, int, float]]:
    """Return [(question_number, page_index, y_top), ...] sorted by question number."""
    positions = []
    for pi in range(doc.page_count):
        page = doc[pi]
        blocks = page.get_text("dict")["blocks"]
        for b in blocks:
            if b["type"] != 0:
                continue
            for line in b["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    bbox = span["bbox"]
                    # Question numbers: bold, left margin (x < 70), font size > 12
                    if (
                        text
                        and bbox[0] < 70
                        and span["size"] > 12
                        and (span["flags"] & 16)  # bold
                    ):
                        try:
                            n = int(text)
                        except ValueError:
                            continue
                        if 1 <= n <= 26:
                            positions.append((n, pi, bbox[1]))
    positions.sort(key=lambda t: t[0])
    return positions


def crop_questions(doc: fitz.Document, year: int, out_dir: pathlib.Path) -> list[dict]:
    """Crop individual questions from *doc* and save as PNGs.

    Returns a list of metadata dicts for each successfully cropped question.
    """
    positions = find_question_positions(doc)
    found = {p[0] for p in positions}

    if len(found) < 20:
        print(f"  WARNING: only found {len(found)}/26 question markers for {year}")
        return []

    # Build lookup: page_index -> [(question_number, y_top)] sorted by y
    page_questions: dict[int, list[tuple[int, float]]] = {}
    for qnum, pi, y in positions:
        page_questions.setdefault(pi, []).append((qnum, y))
    for pi in page_questions:
        page_questions[pi].sort(key=lambda t: t[1])

    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    margin_top = 6  # pixels of padding above question number

    for qnum, pi, y_top in positions:
        page = doc[pi]
        pw = page.rect.width
        ph = page.rect.height

        # Find the y_bottom: top of the next question on same page, or page bottom
        pq = page_questions[pi]
        idx = next(i for i, (q, _) in enumerate(pq) if q == qnum)
        if idx + 1 < len(pq):
            y_bottom = pq[idx + 1][1] - 2
        else:
            y_bottom = ph

        # Clamp and add small margin
        y_start = max(0, y_top - margin_top)
        y_end = min(ph, y_bottom)

        if y_end - y_start < 20:
            continue

        clip = fitz.Rect(0, y_start, pw, y_end)
        pix = page.get_pixmap(clip=clip, dpi=DPI)
        img_path = out_dir / f"q{qnum}.png"
        pix.save(str(img_path))
        results.append(
            {
                "year": year,
                "number": qnum,
                "difficulty": DIFFICULTY[qnum],
                "image": f"img/{year}/q{qnum}.png",
            }
        )

    return results


# ---------------------------------------------------------------------------
# HTML game generator
# ---------------------------------------------------------------------------


def generate_game_html(questions: list[dict]) -> str:
    """Return the complete HTML for the Kangourou game page."""
    data_json = json.dumps(questions, ensure_ascii=False)
    return GAME_HTML_TEMPLATE.replace("__QUESTION_DATA__", data_json)


GAME_HTML_TEMPLATE = r"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kangourou des Mathématiques — Jeu</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #fff;
      --fg: #222;
      --card-bg: #f9f9f9;
      --border: #ddd;
      --btn-bg: #eee;
      --btn-hover: #ddd;
      --facile: #2e7d32;
      --moyen: #1565c0;
      --difficile: #e65100;
      --expert: #b71c1c;
      --correct: #4caf50;
      --wrong: #f44336;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #1a1a1a;
        --fg: #e0e0e0;
        --card-bg: #2a2a2a;
        --border: #444;
        --btn-bg: #333;
        --btn-hover: #444;
      }
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: system-ui, sans-serif;
      background: var(--bg);
      color: var(--fg);
      line-height: 1.6;
      min-height: 100vh;
    }
    .container {
      max-width: 56rem;
      margin: 0 auto;
      padding: 1rem;
    }
    header {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 0.5rem;
      padding: 0.75rem 0;
      border-bottom: 2px solid var(--border);
      margin-bottom: 1rem;
    }
    header h1 {
      font-size: 1.3rem;
      font-weight: 700;
    }
    .stats-bar {
      display: flex;
      gap: 1.2rem;
      font-size: 0.95rem;
      font-variant-numeric: tabular-nums;
    }
    .stats-bar span { white-space: nowrap; }
    .badge {
      display: inline-block;
      padding: 0.15rem 0.6rem;
      border-radius: 4px;
      color: #fff;
      font-size: 0.85rem;
      font-weight: 600;
      text-transform: capitalize;
    }
    .badge-facile   { background: var(--facile); }
    .badge-moyen    { background: var(--moyen); }
    .badge-difficile{ background: var(--difficile); }
    .badge-expert   { background: var(--expert); }

    #game-area {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 1rem;
    }
    .question-info {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      font-size: 1rem;
    }
    .question-img-wrap {
      width: 100%;
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      text-align: center;
    }
    .question-img-wrap img {
      max-width: 100%;
      height: auto;
      display: block;
      margin: 0 auto;
    }
    .answers {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      justify-content: center;
    }
    .answers button {
      font-size: 1.15rem;
      font-weight: 600;
      min-width: 3.2rem;
      padding: 0.6rem 1.1rem;
      border: 2px solid var(--border);
      border-radius: 8px;
      background: var(--btn-bg);
      color: var(--fg);
      cursor: pointer;
      transition: background 0.15s, border-color 0.15s, transform 0.1s;
    }
    .answers button:hover { background: var(--btn-hover); transform: scale(1.05); }
    .answers button:active { transform: scale(0.97); }
    .answers button.correct {
      background: var(--correct) !important;
      border-color: var(--correct) !important;
      color: #fff !important;
    }
    .answers button.wrong {
      background: var(--wrong) !important;
      border-color: var(--wrong) !important;
      color: #fff !important;
    }
    .answers button.selected {
      border-color: var(--moyen) !important;
      background: color-mix(in srgb, var(--moyen) 25%, var(--btn-bg)) !important;
    }
    .action-buttons {
      display: flex;
      gap: 0.75rem;
      justify-content: center;
      flex-wrap: wrap;
    }
    .action-buttons button {
      font-size: 1rem;
      padding: 0.5rem 1.5rem;
      border: 2px solid var(--border);
      border-radius: 8px;
      background: var(--btn-bg);
      color: var(--fg);
      cursor: pointer;
      transition: background 0.15s;
    }
    .action-buttons button:hover { background: var(--btn-hover); }

    /* Start screen */
    #start-screen {
      text-align: center;
      padding: 3rem 1rem;
    }
    #start-screen h2 { font-size: 1.6rem; margin-bottom: 1rem; }
    #start-screen p { margin-bottom: 1.5rem; color: #666; }
    .start-buttons { display: flex; gap: 1rem; justify-content: center; flex-wrap: wrap; }
    .start-buttons button {
      font-size: 1.2rem;
      padding: 0.75rem 2.5rem;
      border: none;
      border-radius: 8px;
      color: #fff;
      cursor: pointer;
      transition: opacity 0.2s;
    }
    .start-buttons button:hover { opacity: 0.85; }

    /* Statistics panel */
    #stats-panel {
      display: none;
      width: 100%;
      max-width: 56rem;
      margin: 0 auto;
    }
    #stats-panel h2 { text-align: center; margin-bottom: 1rem; }
    .stats-table {
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 1.5rem;
      font-size: 0.95rem;
    }
    .stats-table th, .stats-table td {
      padding: 0.5rem 0.75rem;
      border: 1px solid var(--border);
      text-align: center;
    }
    .stats-table th {
      background: var(--card-bg);
      font-weight: 600;
    }
    .stats-actions {
      display: flex;
      gap: 0.75rem;
      justify-content: center;
      margin-top: 1.5rem;
    }
    .stats-actions button {
      font-size: 1rem;
      padding: 0.5rem 1.5rem;
      border: 2px solid var(--border);
      border-radius: 8px;
      background: var(--btn-bg);
      color: var(--fg);
      cursor: pointer;
    }
    .stats-actions button:hover { background: var(--btn-hover); }
    .stats-actions button.primary {
      background: var(--moyen);
      border-color: var(--moyen);
      color: #fff;
    }
    .score-total {
      text-align: center;
      font-size: 1.3rem;
      font-weight: 700;
      margin-bottom: 1rem;
    }
    .timer-display {
      font-variant-numeric: tabular-nums;
    }
    .stats-section { margin-bottom: 1.5rem; }
    .stats-section h3 { margin-bottom: 0.5rem; font-size: 1.05rem; }
    .stats-detail-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9rem;
      margin-bottom: 0.5rem;
    }
    .stats-detail-table th, .stats-detail-table td {
      padding: 0.35rem 0.6rem;
      border: 1px solid var(--border);
      text-align: left;
    }
    .stats-detail-table th {
      background: var(--card-bg);
      font-weight: 600;
    }
    .stats-detail-table a { color: var(--moyen); }
    .histogram-wrap {
      width: 100%;
      overflow-x: auto;
      margin-bottom: 1rem;
    }
    .histogram-wrap canvas {
      width: 100%;
      max-width: 50rem;
      height: 220px;
      display: block;
      margin: 0 auto;
    }
    /* Kangourou nav */
    .k-nav {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      justify-content: center;
      margin-bottom: 0.5rem;
    }
    .k-nav button {
      width: 2rem;
      height: 2rem;
      padding: 0;
      font-size: 0.8rem;
      font-weight: 600;
      border: 2px solid var(--fg);
      border-radius: 50%;
      background: transparent;
      color: var(--fg);
      cursor: pointer;
      transition: background 0.15s, border-color 0.15s, color 0.15s;
      line-height: 1;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .k-nav button:hover { background: color-mix(in srgb, var(--fg) 10%, transparent); }
    .k-nav button.k-current { border-color: var(--moyen); color: var(--moyen); box-shadow: 0 0 0 2px color-mix(in srgb, var(--moyen) 30%, transparent); }
    .k-nav button.k-answered { background: var(--fg); color: var(--bg); border-color: var(--fg); }
    .k-nav button.k-answered.k-current { background: var(--moyen); color: #fff; border-color: var(--moyen); }
    .k-countdown { color: var(--wrong); font-weight: 700; }

    @media (max-width: 600px) {
      header h1 { font-size: 1.1rem; }
      .stats-bar { font-size: 0.85rem; gap: 0.7rem; }
      .answers button { min-width: 2.6rem; padding: 0.5rem 0.8rem; font-size: 1rem; }
    }
  </style>
</head>
<body>
<div class="container">
  <header>
    <h1>Kangourou des Mathématiques</h1>
    <div class="stats-bar" id="stats-bar" style="display:none">
      <span id="score-wrap">Score : <strong id="score-display">0</strong></span>
      <span>Question : <strong id="count-display">0</strong></span>
      <span class="timer-display">Temps : <strong id="timer-display">0:00</strong></span>
    </div>
  </header>

  <div id="start-screen">
    <h2>Entraînement Kangourou — Sujet C</h2>
    <p>
      Questions issues des concours Kangourou des mathématiques.<br>
      Répondez aux questions, passez celles que vous ne savez pas,<br>
      et cliquez sur <em>Arrêter</em> pour voir vos statistiques.
    </p>
    <div class="start-buttons">
      <button style="background:var(--moyen)" onclick="startGame()">Entraînement libre</button>
      <button style="background:var(--expert)" onclick="startKangourou()">Mode Kangourou</button>
    </div>
  </div>

  <div id="game-area" style="display:none">
    <div id="k-nav-bar" class="k-nav" style="display:none"></div>
    <div class="question-info">
      <span id="q-badge" class="badge"></span>
      <span id="q-info"></span>
    </div>
    <div class="question-img-wrap">
      <img id="q-img" alt="Question" />
    </div>
    <div class="answers" id="answers-area"></div>
    <div class="action-buttons" id="action-buttons">
      <button onclick="skipQuestion()">Passer</button>
      <button onclick="stopGame()">Arrêter</button>
    </div>
  </div>

  <div id="stats-panel">
    <h2>Résultats</h2>
    <div class="score-total" id="final-score"></div>
    <table class="stats-table">
      <thead>
        <tr>
          <th>Difficulté</th>
          <th>Tentées</th>
          <th>Correctes</th>
          <th>Taux</th>
          <th>Temps total</th>
          <th>Temps moyen</th>
        </tr>
      </thead>
      <tbody id="stats-body"></tbody>
    </table>
    <div id="stats-mistakes" class="stats-section"></div>
    <div id="stats-slowest" class="stats-section"></div>
    <div id="stats-histogram" class="stats-section"></div>
    <div class="stats-actions" id="stats-actions">
      <button onclick="resumeGame()">Reprendre</button>
      <button class="primary" onclick="newGame()">Nouvelle partie</button>
    </div>
  </div>
</div>

<script>
const ALL_QUESTIONS = __QUESTION_DATA__;

const SCORING = {
  facile:    { correct:  3,   wrong: -0.75 },
  moyen:     { correct:  4,   wrong: -1    },
  difficile: { correct:  5,   wrong: -1.25 },
  expert:    { correct: 10,   wrong:  0    },
};

const DIFF_LABELS = {
  facile: 'Facile (Q1\u20138)',
  moyen: 'Moyen (Q9\u201316)',
  difficile: 'Difficile (Q17\u201324)',
  expert: 'Expert (Q25\u201326)',
};

const KANGOUROU_TIME_MS = 50 * 60 * 1000;
const MAX_STATS_ENTRIES = 200;

let pool = [];
let current = null;
let score = 0;
let questionCount = 0;
let questionStartTime = 0;
let gameStartTime = 0;
let timerInterval = null;
let answered = false;
let nextQuestionTimer = null;
let pauseStart = 0;
let totalPausedMs = 0;
let questionLog = [];

// Per-difficulty tracking
let stats = {};

// Kangourou mode state
let kangourouMode = false;
let kQuestions = [];
let kAnswers = [];
let kTimes = [];
let kIndex = 0;
let kFinished = false;

function resetStats() {
  stats = {};
  for (const d of ['facile', 'moyen', 'difficile', 'expert']) {
    stats[d] = { attempted: 0, correct: 0, timeMs: 0 };
  }
  questionLog = [];
}

function shuffle(arr) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

function refillPool() {
  pool = shuffle([...ALL_QUESTIONS]);
}

function formatTime(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return m + ':' + String(sec).padStart(2, '0');
}

function updateTimerDisplay() {
  if (kangourouMode) {
    const elapsed = Date.now() - gameStartTime - totalPausedMs;
    const remaining = Math.max(0, KANGOUROU_TIME_MS - elapsed);
    const el = document.getElementById('timer-display');
    el.textContent = formatTime(remaining);
    if (remaining <= 5 * 60 * 1000) {
      el.classList.add('k-countdown');
    } else {
      el.classList.remove('k-countdown');
    }
    if (remaining <= 0) {
      finishKangourou();
    }
  } else {
    const elapsed = Date.now() - gameStartTime - totalPausedMs;
    const el = document.getElementById('timer-display');
    el.textContent = formatTime(elapsed);
    el.classList.remove('k-countdown');
  }
}

function startGame() {
  kangourouMode = false;
  kFinished = false;
  document.getElementById('start-screen').style.display = 'none';
  document.getElementById('game-area').style.display = 'flex';
  document.getElementById('stats-bar').style.display = 'flex';
  document.getElementById('stats-panel').style.display = 'none';
  document.getElementById('k-nav-bar').style.display = 'none';
  document.getElementById('score-wrap').style.display = '';
  document.getElementById('action-buttons').innerHTML =
    '<button onclick="skipQuestion()">Passer</button>' +
    '<button onclick="stopGame()">Arr\u00eater</button>';
  score = 0;
  questionCount = 0;
  totalPausedMs = 0;
  pauseStart = 0;
  resetStats();
  refillPool();
  gameStartTime = Date.now();
  updateTimerDisplay();
  timerInterval = setInterval(updateTimerDisplay, 1000);
  updateScoreDisplay();
  nextQuestion();
}

function startKangourou() {
  kangourouMode = true;
  kFinished = false;
  document.getElementById('start-screen').style.display = 'none';
  document.getElementById('game-area').style.display = 'flex';
  document.getElementById('stats-bar').style.display = 'flex';
  document.getElementById('stats-panel').style.display = 'none';
  document.getElementById('score-wrap').style.display = 'none';
  document.getElementById('k-nav-bar').style.display = 'flex';
  document.getElementById('action-buttons').innerHTML =
    '<button onclick="kSkip()">Passer</button>' +
    '<button onclick="confirmStopKangourou()">Pause</button>';

  // For each question number 1-26, pick one random year
  const byNumber = {};
  for (const q of ALL_QUESTIONS) {
    if (!byNumber[q.number]) byNumber[q.number] = [];
    byNumber[q.number].push(q);
  }
  kQuestions = [];
  for (let n = 1; n <= 26; n++) {
    const candidates = byNumber[n];
    if (candidates && candidates.length > 0) {
      kQuestions.push(candidates[Math.floor(Math.random() * candidates.length)]);
    }
  }
  kAnswers = new Array(kQuestions.length).fill(null);
  kTimes = new Array(kQuestions.length).fill(0);
  kIndex = 0;

  score = 0;
  questionCount = 0;
  totalPausedMs = 0;
  pauseStart = 0;
  resetStats();
  gameStartTime = Date.now();
  updateTimerDisplay();
  timerInterval = setInterval(updateTimerDisplay, 1000);
  updateKNav();
  showKQuestion(0);
  updateScoreDisplay();
}

function updateKNav() {
  const bar = document.getElementById('k-nav-bar');
  bar.innerHTML = '';
  for (let i = 0; i < kQuestions.length; i++) {
    const btn = document.createElement('button');
    btn.textContent = i + 1;
    if (i === kIndex) btn.classList.add('k-current');
    if (kAnswers[i] !== null) btn.classList.add('k-answered');
    btn.addEventListener('click', () => {
      saveKTime();
      showKQuestion(i);
    });
    bar.appendChild(btn);
  }
}

function saveKTime() {
  if (questionStartTime > 0) {
    kTimes[kIndex] += Date.now() - questionStartTime;
    questionStartTime = 0;
  }
}

function showKQuestion(idx) {
  kIndex = idx;
  const q = kQuestions[idx];
  current = q;
  answered = kAnswers[idx] !== null;
  questionStartTime = Date.now();

  const badge = document.getElementById('q-badge');
  badge.textContent = q.difficulty;
  badge.className = 'badge badge-' + q.difficulty;
  document.getElementById('q-info').textContent =
    (idx + 1) + '/' + kQuestions.length + ' \u2014 Kangourou ' + q.year + ' \u2014 Q' + q.number;
  document.getElementById('q-img').src = q.image;

  const area = document.getElementById('answers-area');
  area.innerHTML = '';
  const isExpert = q.number >= 25;
  const choices = isExpert
    ? ['0','1','2','3','4','5','6','7','8','9']
    : ['A','B','C','D','E'];
  const alreadyAnswered = kAnswers[idx] !== null;
  for (const ch of choices) {
    const btn = document.createElement('button');
    btn.textContent = ch;
    if (kAnswers[idx] === ch) btn.classList.add('selected');
    if (alreadyAnswered) btn.disabled = true;
    btn.addEventListener('click', () => handleKAnswer(ch));
    area.appendChild(btn);
  }
  updateKNav();
  document.getElementById('count-display').textContent = (idx + 1) + '/' + kQuestions.length;
}

function handleKAnswer(choice) {
  if (kAnswers[kIndex] !== null) return; // already answered
  kAnswers[kIndex] = choice;
  // Highlight selection
  const area = document.getElementById('answers-area');
  for (const btn of area.children) {
    btn.classList.toggle('selected', btn.textContent === choice);
    btn.disabled = true;
  }
  updateKNav();
  // Auto-advance to next unanswered question
  saveKTime();
  const next = findNextUnanswered(kIndex);
  if (next !== -1) {
    setTimeout(() => showKQuestion(next), 400);
  } else {
    // All answered — finish
    setTimeout(() => finishKangourou(), 400);
  }
}

function findNextUnanswered(fromIndex) {
  for (let i = 1; i <= kQuestions.length; i++) {
    const ni = (fromIndex + i) % kQuestions.length;
    if (kAnswers[ni] === null) return ni;
  }
  return -1;
}

function kSkip() {
  saveKTime();
  const next = findNextUnanswered(kIndex);
  if (next !== -1) {
    showKQuestion(next);
  }
}

function confirmStopKangourou() {
  saveKTime();
  clearInterval(timerInterval);
  pauseStart = Date.now();
  document.getElementById('game-area').style.display = 'none';
  document.getElementById('stats-panel').style.display = 'block';

  // Show interim stats
  const answeredCount = kAnswers.filter(a => a !== null).length;
  document.getElementById('final-score').textContent =
    answeredCount + '/' + kQuestions.length + ' r\u00e9pondues \u2014 Pause';

  const actionsDiv = document.getElementById('stats-actions');
  actionsDiv.innerHTML =
    '<button class="primary" onclick="resumeKangourou()">Reprendre</button>' +
    '<button onclick="finishKangourou()">Terminer</button>';

  // Compute interim summary table
  const tempStats = {};
  for (const d of ['facile', 'moyen', 'difficile', 'expert']) {
    tempStats[d] = { attempted: 0, correct: 0, timeMs: 0 };
  }
  for (let i = 0; i < kQuestions.length; i++) {
    const q = kQuestions[i];
    const d = q.difficulty;
    const ans = kAnswers[i];
    if (ans !== null) {
      tempStats[d].attempted++;
      tempStats[d].timeMs += kTimes[i];
      if (ans === q.answer) tempStats[d].correct++;
    }
  }
  const tbody = document.getElementById('stats-body');
  tbody.innerHTML = '';
  for (const d of ['facile', 'moyen', 'difficile', 'expert']) {
    const s = tempStats[d];
    const tr = document.createElement('tr');
    const rate = s.attempted > 0
      ? Math.round(100 * s.correct / s.attempted) + '%' : '\u2014';
    const avg = s.attempted > 0
      ? formatTime(Math.round(s.timeMs / s.attempted)) : '\u2014';
    tr.innerHTML =
      '<td>' + DIFF_LABELS[d] + '</td>' +
      '<td>' + s.attempted + '</td>' +
      '<td>' + s.correct + '</td>' +
      '<td>' + rate + '</td>' +
      '<td>' + formatTime(s.timeMs) + '</td>' +
      '<td>' + avg + '</td>';
    tbody.appendChild(tr);
  }
  document.getElementById('stats-mistakes').innerHTML = '';
  document.getElementById('stats-slowest').innerHTML = '';
  document.getElementById('stats-histogram').innerHTML = '';
}

function resumeKangourou() {
  if (pauseStart) {
    totalPausedMs += Date.now() - pauseStart;
    pauseStart = 0;
  }
  document.getElementById('stats-panel').style.display = 'none';
  document.getElementById('game-area').style.display = 'flex';
  timerInterval = setInterval(updateTimerDisplay, 1000);
  updateTimerDisplay();
  showKQuestion(kIndex);
}

function finishKangourou() {
  if (kFinished) return;
  kFinished = true;
  saveKTime();
  clearInterval(timerInterval);
  // Compute score and stats from kAnswers
  score = 0;
  questionCount = 0;
  resetStats();
  questionLog = [];
  for (let i = 0; i < kQuestions.length; i++) {
    const q = kQuestions[i];
    const d = q.difficulty;
    const ans = kAnswers[i];
    const elapsed = kTimes[i];
    if (ans !== null) {
      stats[d].attempted++;
      stats[d].timeMs += elapsed;
      questionCount++;
      const ok = ans === q.answer;
      if (ok) {
        score += SCORING[d].correct;
        stats[d].correct++;
      } else {
        score += SCORING[d].wrong;
      }
      questionLog.push({
        question: q, userAnswer: ans, correct: ok,
        timeMs: elapsed, skipped: false
      });
    } else {
      // skipped
      stats[d].timeMs += elapsed;
      questionLog.push({
        question: q, userAnswer: null, correct: false,
        timeMs: elapsed, skipped: true
      });
    }
  }
  showStats();
}

function newGame() {
  startGame();
}

function newKangourou() {
  startKangourou();
}

function updateScoreDisplay() {
  document.getElementById('score-display').textContent =
    Number.isInteger(score) ? score : score.toFixed(2);
  if (!kangourouMode) {
    document.getElementById('count-display').textContent = questionCount;
  }
}

function nextQuestion() {
  if (pool.length === 0) refillPool();
  current = pool.pop();
  answered = false;
  questionStartTime = Date.now();

  // Badge
  const badge = document.getElementById('q-badge');
  badge.textContent = current.difficulty;
  badge.className = 'badge badge-' + current.difficulty;

  // Info
  document.getElementById('q-info').textContent =
    'Kangourou ' + current.year + ' \u2014 Question ' + current.number;

  // Image
  document.getElementById('q-img').src = current.image;

  // Answer buttons
  const area = document.getElementById('answers-area');
  area.innerHTML = '';
  const isExpert = current.number >= 25;
  const choices = isExpert
    ? ['0','1','2','3','4','5','6','7','8','9']
    : ['A','B','C','D','E'];
  for (const ch of choices) {
    const btn = document.createElement('button');
    btn.textContent = ch;
    btn.addEventListener('click', () => handleAnswer(ch, btn));
    area.appendChild(btn);
  }
}

function handleAnswer(choice, btn) {
  if (answered) return;
  answered = true;
  const elapsed = Date.now() - questionStartTime;
  const diff = current.difficulty;
  stats[diff].attempted++;
  stats[diff].timeMs += elapsed;
  questionCount++;

  const isCorrect = choice === current.answer;
  if (isCorrect) {
    score += SCORING[diff].correct;
    stats[diff].correct++;
    btn.classList.add('correct');
  } else {
    score += SCORING[diff].wrong;
    btn.classList.add('wrong');
    // Highlight the correct answer
    const buttons = document.getElementById('answers-area').children;
    for (const b of buttons) {
      if (b.textContent === current.answer) {
        b.classList.add('correct');
      }
    }
  }
  questionLog.push({
    question: { ...current }, userAnswer: choice, correct: isCorrect,
    timeMs: elapsed, skipped: false
  });
  updateScoreDisplay();
  nextQuestionTimer = setTimeout(nextQuestion, isCorrect ? 600 : 1200);
}

function skipQuestion() {
  if (answered) return;
  const elapsed = Date.now() - questionStartTime;
  const diff = current.difficulty;
  stats[diff].timeMs += elapsed;
  questionCount++;
  questionLog.push({
    question: { ...current }, userAnswer: null, correct: false,
    timeMs: elapsed, skipped: true
  });
  updateScoreDisplay();
  nextQuestion();
}

function stopGame() {
  clearInterval(timerInterval);
  clearTimeout(nextQuestionTimer);
  nextQuestionTimer = null;
  pauseStart = Date.now();
  showStats();
}

function showStats() {
  document.getElementById('game-area').style.display = 'none';
  document.getElementById('stats-panel').style.display = 'block';

  document.getElementById('final-score').textContent =
    'Score : ' + (Number.isInteger(score) ? score : score.toFixed(2));

  // Summary table
  const tbody = document.getElementById('stats-body');
  tbody.innerHTML = '';
  for (const d of ['facile', 'moyen', 'difficile', 'expert']) {
    const s = stats[d];
    const tr = document.createElement('tr');
    const rate = s.attempted > 0
      ? Math.round(100 * s.correct / s.attempted) + '%'
      : '\u2014';
    const avg = s.attempted > 0
      ? formatTime(Math.round(s.timeMs / s.attempted))
      : '\u2014';
    tr.innerHTML =
      '<td>' + DIFF_LABELS[d] + '</td>' +
      '<td>' + s.attempted + '</td>' +
      '<td>' + s.correct + '</td>' +
      '<td>' + rate + '</td>' +
      '<td>' + formatTime(s.timeMs) + '</td>' +
      '<td>' + avg + '</td>';
    tbody.appendChild(tr);
  }

  // Mistakes section
  const mistakesDiv = document.getElementById('stats-mistakes');
  const allMistakes = questionLog.filter(e => !e.correct && !e.skipped);
  const freeMode = !kangourouMode;
  const capMistakes = freeMode && allMistakes.length > MAX_STATS_ENTRIES;
  const mistakes = capMistakes ? allMistakes.slice(-MAX_STATS_ENTRIES) : allMistakes;
  if (mistakes.length > 0) {
    let h = '<h3>Erreurs</h3>';
    if (capMistakes) {
      h += '<p style="font-size:0.85rem;color:#888">' +
        allMistakes.length + ' erreurs au total \u2014 ' + MAX_STATS_ENTRIES + ' plus r\u00e9centes affich\u00e9es.' +
        ' <button onclick="showAllMistakes()" style="border:none;background:none;color:var(--moyen);' +
        'cursor:pointer;text-decoration:underline;font-size:0.85rem">Tout afficher</button></p>';
    }
    h += '<table class="stats-detail-table"><thead><tr>' +
      '<th>Question</th><th>Difficulté</th><th>Votre réponse</th><th>Bonne réponse</th><th>Temps</th>' +
      '</tr></thead><tbody>';
    for (const m of mistakes) {
      const q = m.question;
      const label = 'Kangourou ' + q.year + ' Q' + q.number;
      h += '<tr>' +
        '<td><a href="' + q.image + '" target="_blank" rel="noopener noreferrer">' + label + '</a></td>' +
        '<td><span class="badge badge-' + q.difficulty + '">' + q.difficulty + '</span></td>' +
        '<td><strong style="color:var(--wrong)">' + m.userAnswer + '</strong></td>' +
        '<td><strong style="color:var(--correct)">' + q.answer + '</strong></td>' +
        '<td>' + formatTime(m.timeMs) + '</td></tr>';
    }
    h += '</tbody></table>';
    mistakesDiv.innerHTML = h;
  } else {
    mistakesDiv.innerHTML = '';
  }

  // Slowest correct/skipped per category (2 per category)
  const slowestDiv = document.getElementById('stats-slowest');
  let slowHtml = '';
  for (const d of ['facile', 'moyen', 'difficile', 'expert']) {
    const entries = questionLog.filter(e =>
      e.question.difficulty === d && (e.correct || e.skipped)
    );
    entries.sort((a, b) => b.timeMs - a.timeMs);
    const top2 = entries.slice(0, 2);
    if (top2.length > 0) {
      slowHtml += '<h3>Plus lentes (' + DIFF_LABELS[d] + ')</h3>' +
        '<table class="stats-detail-table"><thead><tr>' +
        '<th>Question</th><th>Temps</th><th>Résultat</th>' +
        '</tr></thead><tbody>';
      for (const e of top2) {
        const q = e.question;
        const label = 'Kangourou ' + q.year + ' Q' + q.number;
        const res = e.skipped ? 'Passée' : 'Correcte';
        slowHtml += '<tr>' +
          '<td><a href="' + q.image + '" target="_blank" rel="noopener noreferrer">' + label + '</a></td>' +
          '<td>' + formatTime(e.timeMs) + '</td>' +
          '<td>' + res + '</td></tr>';
      }
      slowHtml += '</tbody></table>';
    }
  }
  slowestDiv.innerHTML = slowHtml;

  // Histogram of answer times
  const histDiv = document.getElementById('stats-histogram');
  const allAnswered = questionLog.filter(e => !e.skipped);
  const answered_entries = freeMode && allAnswered.length > MAX_STATS_ENTRIES
    ? allAnswered.slice(-MAX_STATS_ENTRIES) : allAnswered;
  if (answered_entries.length > 0) {
    histDiv.innerHTML = '<h3>Distribution des temps de réponse</h3>' +
      '<div class="histogram-wrap"><canvas id="hist-canvas"></canvas></div>';
    drawHistogram(answered_entries);
  } else {
    histDiv.innerHTML = '';
  }

  // Actions
  const actionsDiv = document.getElementById('stats-actions');
  if (kangourouMode) {
    actionsDiv.innerHTML =
      '<button class="primary" onclick="newKangourou()">Nouveau Kangourou</button>' +
      '<button onclick="backToMenu()">Menu</button>';
  } else {
    actionsDiv.innerHTML =
      '<button onclick="resumeGame()">Reprendre</button>' +
      '<button class="primary" onclick="newGame()">Nouvelle partie</button>';
  }
}

function showAllMistakes() {
  const mistakesDiv = document.getElementById('stats-mistakes');
  const allMistakes = questionLog.filter(e => !e.correct && !e.skipped);
  let h = '<h3>Erreurs (' + allMistakes.length + ')</h3>' +
    '<table class="stats-detail-table"><thead><tr>' +
    '<th>Question</th><th>Difficulté</th><th>Votre réponse</th><th>Bonne réponse</th><th>Temps</th>' +
    '</tr></thead><tbody>';
  for (const m of allMistakes) {
    const q = m.question;
    const label = 'Kangourou ' + q.year + ' Q' + q.number;
    h += '<tr>' +
      '<td><a href="' + q.image + '" target="_blank" rel="noopener noreferrer">' + label + '</a></td>' +
      '<td><span class="badge badge-' + q.difficulty + '">' + q.difficulty + '</span></td>' +
      '<td><strong style="color:var(--wrong)">' + m.userAnswer + '</strong></td>' +
      '<td><strong style="color:var(--correct)">' + q.answer + '</strong></td>' +
      '<td>' + formatTime(m.timeMs) + '</td></tr>';
  }
  h += '</tbody></table>';
  mistakesDiv.innerHTML = h;
}

function drawHistogram(entries) {
  const canvas = document.getElementById('hist-canvas');
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 500;
  const h = canvas.clientHeight || 220;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  // Compute buckets (5s intervals)
  const times = entries.map(e => e.timeMs / 1000);
  const maxT = Math.max(...times);
  const bucketSize = 5;
  const nBuckets = Math.max(1, Math.ceil(maxT / bucketSize));
  const buckets = new Array(nBuckets).fill(0);
  const bucketColors = [];
  for (let i = 0; i < nBuckets; i++) bucketColors.push([]);
  const diffColorMap = {
    facile: getComputedStyle(document.documentElement).getPropertyValue('--facile').trim(),
    moyen: getComputedStyle(document.documentElement).getPropertyValue('--moyen').trim(),
    difficile: getComputedStyle(document.documentElement).getPropertyValue('--difficile').trim(),
    expert: getComputedStyle(document.documentElement).getPropertyValue('--expert').trim(),
  };
  for (const e of entries) {
    const bi = Math.min(nBuckets - 1, Math.floor(e.timeMs / 1000 / bucketSize));
    buckets[bi]++;
    bucketColors[bi].push(e.question.difficulty);
  }
  const maxCount = Math.max(...buckets);

  const padL = 35, padR = 10, padT = 10, padB = 30;
  const chartW = w - padL - padR;
  const chartH = h - padT - padB;
  const barW = Math.max(4, chartW / nBuckets - 2);

  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--fg').trim();
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'right';

  // Y axis labels
  const ySteps = Math.min(maxCount, 5);
  for (let i = 0; i <= ySteps; i++) {
    const val = Math.round(maxCount * i / ySteps);
    const y = padT + chartH - (chartH * i / ySteps);
    ctx.fillText(val, padL - 5, y + 4);
    ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--border').trim();
    ctx.lineWidth = 0.5;
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(w - padR, y);
    ctx.stroke();
  }

  // Bars - stacked by difficulty
  ctx.textAlign = 'center';
  for (let i = 0; i < nBuckets; i++) {
    if (buckets[i] === 0) continue;
    const x = padL + (chartW * i / nBuckets) + 1;
    const totalH = (buckets[i] / maxCount) * chartH;
    // Count per difficulty in this bucket
    const counts = {};
    for (const d of bucketColors[i]) counts[d] = (counts[d] || 0) + 1;
    let yOff = 0;
    for (const d of ['facile', 'moyen', 'difficile', 'expert']) {
      if (!counts[d]) continue;
      const segH = (counts[d] / buckets[i]) * totalH;
      ctx.fillStyle = diffColorMap[d];
      ctx.fillRect(x, padT + chartH - yOff - segH, barW, segH);
      yOff += segH;
    }
    // X label
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--fg').trim();
    if (nBuckets <= 20 || i % Math.ceil(nBuckets / 20) === 0) {
      ctx.fillText(i * bucketSize + 's', x + barW / 2, h - padB + 15);
    }
  }
}

function backToMenu() {
  document.getElementById('stats-panel').style.display = 'none';
  document.getElementById('start-screen').style.display = 'block';
  document.getElementById('stats-bar').style.display = 'none';
  document.getElementById('k-nav-bar').style.display = 'none';
}

function resumeGame() {
  if (pauseStart) {
    totalPausedMs += Date.now() - pauseStart;
    pauseStart = 0;
  }
  document.getElementById('stats-panel').style.display = 'none';
  document.getElementById('game-area').style.display = 'flex';
  timerInterval = setInterval(updateTimerDisplay, 1000);
  if (answered || !current) nextQuestion();
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- Check if game data already exists ---
    data_path = OUT_DIR / "data.json"
    html_path = OUT_DIR / "index.html"
    if data_path.exists() and html_path.exists():
        try:
            existing = json.loads(data_path.read_text())
            # Verify all referenced images exist
            all_present = existing and all(
                (OUT_DIR / q["image"]).exists() for q in existing
            )
            if all_present:
                print(f"Game data already complete ({len(existing)} questions). Nothing to do.")
                return
        except (json.JSONDecodeError, KeyError):
            pass  # regenerate on corrupt data

    all_questions: list[dict] = []
    skipped_years: list[int] = []
    last_request = 0.0

    for year in YEARS:
        print(f"\n{'='*50}")
        print(f"Processing {year}...")

        # --- throttle ---
        def throttle():
            nonlocal last_request
            elapsed = time.time() - last_request
            if elapsed < REQUEST_DELAY:
                time.sleep(REQUEST_DELAY - elapsed)
            last_request = time.time()

        # --- Download PDF ---
        pdf_path = TMP_DIR / f"kangourou{year}c.pdf"
        if not pdf_path.exists():
            throttle()
            try:
                print(f"  Downloading PDF for {year}...")
                pdf_data = fetch(pdf_url(year), binary=True)
                pdf_path.write_bytes(pdf_data)
            except Exception as exc:
                print(f"  SKIP {year}: PDF download failed: {exc}")
                skipped_years.append(year)
                continue
        else:
            print(f"  PDF already cached for {year}")

        # --- Download solutions ---
        throttle()
        try:
            print(f"  Downloading solutions for {year}...")
            sol_html = fetch(SOL_URL.format(year))
        except Exception as exc:
            print(f"  SKIP {year}: solutions download failed: {exc}")
            skipped_years.append(year)
            continue

        answers = parse_answers(sol_html)
        if len(answers) < 24:
            print(f"  SKIP {year}: only found {len(answers)} answers (need >= 24)")
            skipped_years.append(year)
            continue
        print(f"  Found {len(answers)} answers")

        # --- Crop questions ---
        try:
            doc = fitz.open(str(pdf_path))
        except Exception as exc:
            print(f"  SKIP {year}: cannot open PDF: {exc}")
            skipped_years.append(year)
            continue

        img_out = IMG_DIR / str(year)
        results = crop_questions(doc, year, img_out)
        doc.close()

        if not results:
            print(f"  SKIP {year}: question cropping failed")
            skipped_years.append(year)
            continue

        # Add answers to results
        for q in results:
            n = q["number"]
            if n in answers:
                q["answer"] = answers[n]
            else:
                print(f"  WARNING: no answer for Q{n} in {year}")
                q["answer"] = "?"

        # Only keep questions with known answers
        results = [q for q in results if q["answer"] != "?"]
        all_questions.extend(results)
        print(f"  OK: {len(results)} questions extracted")

    print(f"\n{'='*50}")
    print(f"Total questions: {len(all_questions)}")

    if skipped_years:
        print(f"ERROR: failed to process years: {skipped_years}")
        sys.exit(1)

    if not all_questions:
        print("ERROR: no questions extracted, aborting")
        sys.exit(1)

    # --- Generate data.json ---
    data_path.write_text(json.dumps(all_questions, ensure_ascii=False, indent=2))
    print(f"Wrote {data_path}")

    # --- Generate game HTML ---
    html_path.write_text(generate_game_html(all_questions))
    print(f"Wrote {html_path}")

    print("\nDone! Game files are in kangourou/")


if __name__ == "__main__":
    main()

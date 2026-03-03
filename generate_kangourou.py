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
    .action-buttons {
      display: flex;
      gap: 0.75rem;
      justify-content: center;
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
    #start-screen button {
      font-size: 1.2rem;
      padding: 0.75rem 2.5rem;
      border: none;
      border-radius: 8px;
      background: var(--moyen);
      color: #fff;
      cursor: pointer;
      transition: opacity 0.2s;
    }
    #start-screen button:hover { opacity: 0.85; }

    /* Statistics panel */
    #stats-panel {
      display: none;
      width: 100%;
      max-width: 40rem;
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
      <span>Score : <strong id="score-display">0</strong></span>
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
    <button onclick="startGame()">Commencer</button>
  </div>

  <div id="game-area" style="display:none">
    <div class="question-info">
      <span id="q-badge" class="badge"></span>
      <span id="q-info"></span>
    </div>
    <div class="question-img-wrap">
      <img id="q-img" alt="Question" />
    </div>
    <div class="answers" id="answers-area"></div>
    <div class="action-buttons">
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
    <div class="stats-actions">
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
  facile: 'Facile (Q1–8)',
  moyen: 'Moyen (Q9–16)',
  difficile: 'Difficile (Q17–24)',
  expert: 'Expert (Q25–26)',
};

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

// Per-difficulty tracking
let stats = {};

function resetStats() {
  stats = {};
  for (const d of ['facile', 'moyen', 'difficile', 'expert']) {
    stats[d] = { attempted: 0, correct: 0, timeMs: 0 };
  }
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
  const elapsed = Date.now() - gameStartTime - totalPausedMs;
  document.getElementById('timer-display').textContent = formatTime(elapsed);
}

function startGame() {
  document.getElementById('start-screen').style.display = 'none';
  document.getElementById('game-area').style.display = 'flex';
  document.getElementById('stats-bar').style.display = 'flex';
  document.getElementById('stats-panel').style.display = 'none';
  score = 0;
  questionCount = 0;
  totalPausedMs = 0;
  pauseStart = 0;
  resetStats();
  refillPool();
  gameStartTime = Date.now();
  timerInterval = setInterval(updateTimerDisplay, 1000);
  updateScoreDisplay();
  nextQuestion();
}

function newGame() {
  startGame();
}

function updateScoreDisplay() {
  document.getElementById('score-display').textContent =
    Number.isInteger(score) ? score : score.toFixed(2);
  document.getElementById('count-display').textContent = questionCount;
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
    'Kangourou ' + current.year + ' — Question ' + current.number;

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
  updateScoreDisplay();
  nextQuestionTimer = setTimeout(nextQuestion, isCorrect ? 600 : 1200);
}

function skipQuestion() {
  if (answered) return;
  const elapsed = Date.now() - questionStartTime;
  const diff = current.difficulty;
  stats[diff].timeMs += elapsed;
  questionCount++;
  updateScoreDisplay();
  nextQuestion();
}

function stopGame() {
  clearInterval(timerInterval);
  clearTimeout(nextQuestionTimer);
  nextQuestionTimer = null;
  pauseStart = Date.now();
  document.getElementById('game-area').style.display = 'none';
  document.getElementById('stats-panel').style.display = 'block';

  document.getElementById('final-score').textContent =
    'Score : ' + (Number.isInteger(score) ? score : score.toFixed(2));

  const tbody = document.getElementById('stats-body');
  tbody.innerHTML = '';
  for (const d of ['facile', 'moyen', 'difficile', 'expert']) {
    const s = stats[d];
    const tr = document.createElement('tr');
    const rate = s.attempted > 0
      ? Math.round(100 * s.correct / s.attempted) + '%'
      : '—';
    const avg = s.attempted > 0
      ? formatTime(Math.round(s.timeMs / s.attempted))
      : '—';
    tr.innerHTML =
      '<td>' + DIFF_LABELS[d] + '</td>' +
      '<td>' + s.attempted + '</td>' +
      '<td>' + s.correct + '</td>' +
      '<td>' + rate + '</td>' +
      '<td>' + formatTime(s.timeMs) + '</td>' +
      '<td>' + avg + '</td>';
    tbody.appendChild(tr);
  }
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
    data_path = OUT_DIR / "data.json"
    data_path.write_text(json.dumps(all_questions, ensure_ascii=False, indent=2))
    print(f"Wrote {data_path}")

    # --- Generate game HTML ---
    html_path = OUT_DIR / "index.html"
    html_path.write_text(generate_game_html(all_questions))
    print(f"Wrote {html_path}")

    print("\nDone! Game files are in kangourou/")


if __name__ == "__main__":
    main()

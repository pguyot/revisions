// @ts-check
const { test, expect } = require('@playwright/test');

// ─── Helpers ────────────────────────────────────────────────────────────────

/** Navigate to the DSD app and wait for data to load. */
async function openApp(page) {
  await page.goto('/dsd/');
  // Wait for data.json to load — DATA is a let variable, not on window
  await page.waitForFunction(() => typeof DATA !== 'undefined' && DATA !== null, null, { timeout: 5000 });
  await expect(page.locator('.section-card').first()).toBeVisible();
}

/** Navigate: Start → Part → Exercise list → start exercise at given indices. */
async function startExercise(page, section, teil, exerciseIdx) {
  // Click section card (lv=0, hv=1)
  const sectionIdx = section === 'lv' ? 0 : 1;
  await page.locator('.section-card').nth(sectionIdx).click();
  await expect(page.locator('#part-screen')).toBeVisible();

  // Click part card
  await page.locator('#part-screen .part-card').nth(teil - 1).click();
  await expect(page.locator('#set-screen')).toBeVisible();

  // Click exercise
  await page.locator('#set-list .part-card').nth(exerciseIdx).click();
  await expect(page.locator('#exercise-content')).toBeVisible();
  // Ensure currentExercise is set
  await page.waitForFunction(() => typeof currentExercise !== 'undefined' && currentExercise !== null, null, { timeout: 5000 });
}

/** Read currentExercise from page (let-scoped, not on window). */
function evalExercise(page, fn) {
  return page.evaluate(fn);
}

// ─── Navigation tests ───────────────────────────────────────────────────────

test.describe('Navigation', () => {
  test('start screen shows two section cards', async ({ page }) => {
    await openApp(page);
    const cards = page.locator('.section-card');
    await expect(cards).toHaveCount(2);
    await expect(cards.first()).toContainText('Leseverstehen');
    await expect(cards.last()).toContainText('Hörverstehen');
  });

  test('clicking Leseverstehen shows 5 parts', async ({ page }) => {
    await openApp(page);
    await page.locator('.section-card').first().click();
    await expect(page.locator('#part-screen')).toBeVisible();
    await expect(page.locator('#part-screen .part-card')).toHaveCount(5);
  });

  test('clicking Hörverstehen shows 5 parts', async ({ page }) => {
    await openApp(page);
    await page.locator('.section-card').last().click();
    await expect(page.locator('#part-screen')).toBeVisible();
    await expect(page.locator('#part-screen .part-card')).toHaveCount(5);
  });

  test('Zurück button returns from parts to start', async ({ page }) => {
    await openApp(page);
    await page.locator('.section-card').first().click();
    await expect(page.locator('#part-screen')).toBeVisible();
    await page.locator('#part-screen button', { hasText: 'Zurück' }).click();
    await expect(page.locator('#start-screen')).toBeVisible();
  });

  test('clicking a part shows exercise list', async ({ page }) => {
    await openApp(page);
    await page.locator('.section-card').first().click();
    await page.locator('#part-screen .part-card').first().click();
    await expect(page.locator('#set-screen')).toBeVisible();
    // Should have at least one exercise
    const exercises = page.locator('#set-list .part-card');
    expect(await exercises.count()).toBeGreaterThan(0);
  });

  test('Beenden button returns to start screen', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 3, 0);
    await page.locator('button', { hasText: 'Beenden' }).click();
    await expect(page.locator('#start-screen')).toBeVisible();
  });
});

// ─── LV Teil 1: Lückentext ─────────────────────────────────────────────────

test.describe('LV Teil 1 – Lückentext', () => {
  test('renders gaps, word list, and title options', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 1, 0);

    await expect(page.locator('.gap')).toHaveCount(4);
    // 8 words in word list
    await expect(page.locator('.word-btn')).toHaveCount(8);
    // 3 title options
    await expect(page.locator('#title-options .btn')).toHaveCount(3);
    // 5 progress dots (4 gaps + 1 title)
    await expect(page.locator('.progress-dot')).toHaveCount(5);
  });

  test('can fill gaps and select title, then check for perfect score', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 1, 0);

    // Get the correct answers from the app's data
    const answers = await page.evaluate(() => {
      return { gaps: currentExercise.answers, titleAnswer: currentExercise.titleAnswer };
    });

    // Fill each gap with correct word
    for (const [gapNum, letter] of Object.entries(answers.gaps)) {
      await page.locator(`#gap-${gapNum}`).click();
      await page.locator(`#word-${letter}`).click();
    }

    // Select correct title
    await page.locator(`#title-${answers.titleAnswer}`).click();

    // All progress dots should be filled
    await expect(page.locator('.progress-dot.dot-done')).toHaveCount(5);

    // Submit
    await page.locator('button', { hasText: 'Auswerten' }).click();

    // Results panel
    await expect(page.locator('#results-panel')).toBeVisible();
    await expect(page.locator('#results-score')).toContainText('5 / 5');
    await expect(page.locator('#results-title')).toContainText('Perfekt');
  });
});

// ─── LV Teil 2: Zuordnung ──────────────────────────────────────────────────

test.describe('LV Teil 2 – Zuordnung', () => {
  test('renders persons and ads', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 2, 0);

    expect(await page.locator('.person-item').count()).toBeGreaterThan(0);
    expect(await page.locator('.ad-card').count()).toBeGreaterThan(0);
  });

  test('can match persons to ads and get perfect score', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 2, 0);

    const answers = await page.evaluate(() => {
      return { answers: currentExercise.answers, descIds: currentExercise.descriptions.map(d => d.id) };
    });

    for (const id of answers.descIds) {
      const letter = answers.answers[String(id)];
      await page.locator(`#person-${id}`).click();
      await page.locator(`#ad-${letter}`).click();
    }

    await page.locator('button', { hasText: 'Auswerten' }).click();
    await expect(page.locator('#results-panel')).toBeVisible();
    await expect(page.locator('#results-title')).toContainText('Perfekt');
  });
});

// ─── LV Teil 3: Richtig/Falsch ─────────────────────────────────────────────

test.describe('LV Teil 3 – Richtig/Falsch', () => {
  test('renders text and statements with R/F buttons', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 3, 0);

    await expect(page.locator('.text-box')).toBeVisible();
    expect(await page.locator('.statement-item').count()).toBeGreaterThan(0);
    // Each statement has 2 buttons
    expect(await page.locator('.tf-btn').count()).toBeGreaterThan(0);
  });

  test('perfect score with correct answers', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 3, 0);

    const statements = await page.evaluate(() =>
      currentExercise.statements.map(s => ({ id: s.id, answer: s.answer }))
    );

    for (const s of statements) {
      const suffix = s.answer ? 'r' : 'f';
      await page.locator(`#tf-${s.id}-${suffix}`).click();
    }

    await page.locator('button', { hasText: 'Auswerten' }).click();
    await expect(page.locator('#results-title')).toContainText('Perfekt');
  });

  test('wrong answers show red highlighting', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 3, 0);

    const statements = await page.evaluate(() =>
      currentExercise.statements.map(s => ({ id: s.id, answer: s.answer }))
    );

    // Answer all wrong
    for (const s of statements) {
      const suffix = s.answer ? 'f' : 'r'; // opposite
      await page.locator(`#tf-${s.id}-${suffix}`).click();
    }

    await page.locator('button', { hasText: 'Auswerten' }).click();
    await expect(page.locator('#results-score')).toContainText(`0 / ${statements.length}`);
    // Wrong answers should have tf-wrong class
    await expect(page.locator('.tf-wrong').first()).toBeVisible();
    // Correct answers should be shown with tf-correct class
    await expect(page.locator('.tf-correct').first()).toBeVisible();
  });
});

// ─── LV Teil 4: Multiple Choice ────────────────────────────────────────────

test.describe('LV Teil 4 – Multiple Choice', () => {
  test('renders text and questions with options', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 4, 0);

    await expect(page.locator('.text-box')).toBeVisible();
    expect(await page.locator('.mc-item').count()).toBeGreaterThan(0);
  });

  test('perfect score with correct answers', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 4, 0);

    const questions = await page.evaluate(() =>
      currentExercise.questions.map(q => ({ id: q.id, answer: q.answer }))
    );

    for (const q of questions) {
      await page.locator(`#mc-${q.id}-${q.answer}`).click();
    }

    await page.locator('button', { hasText: 'Auswerten' }).click();
    await expect(page.locator('#results-title')).toContainText('Perfekt');
  });
});

// ─── LV Teil 5: Überschriften ───────────────────────────────────────────────

test.describe('LV Teil 5 – Überschriften', () => {
  test('renders short texts and headings', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 5, 0);

    expect(await page.locator('.short-text-item').count()).toBeGreaterThan(0);
    expect(await page.locator('.heading-card').count()).toBeGreaterThan(0);
  });

  test('perfect score with correct matches', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 5, 0);

    const data = await page.evaluate(() => {
      return { answers: currentExercise.answers, textIds: currentExercise.texts.map(t => t.id) };
    });

    for (const id of data.textIds) {
      const letter = data.answers[String(id)];
      await page.locator(`#st-${id}`).click();
      await page.locator(`#hd-${letter}`).click();
    }

    await page.locator('button', { hasText: 'Auswerten' }).click();
    await expect(page.locator('#results-title')).toContainText('Perfekt');
  });
});

// ─── HV Teil 1: Szenen ─────────────────────────────────────────────────────

test.describe('HV Teil 1 – Szenen', () => {
  test('renders scene with options and navigation', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'hv', 1, 0);

    await expect(page.locator('.scene-opt')).toHaveCount(3);
    // TTS controls should be visible for HV
    await expect(page.locator('#tts-controls')).toBeVisible();
    // Should show "Szene 1 von 5"
    await expect(page.locator('#exercise-content')).toContainText('Szene 1 von');
  });

  test('can navigate through scenes and submit', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'hv', 1, 0);

    const sceneCount = await page.evaluate(() => currentExercise.scenes.length);

    // Navigate through all scenes, picking correct answer
    for (let i = 0; i < sceneCount; i++) {
      const answer = await page.evaluate((idx) => currentExercise.scenes[idx].answer, i);
      await page.locator(`#so-${answer}`).click();

      if (i < sceneCount - 1) {
        await page.locator('button', { hasText: 'Nächste' }).click();
      }
    }

    await page.locator('button', { hasText: 'Auswerten' }).click();
    await expect(page.locator('#results-title')).toContainText('Perfekt');
  });
});

// ─── HV Teil 2: Durchsagen ─────────────────────────────────────────────────

test.describe('HV Teil 2 – Durchsagen', () => {
  test('renders announcement with question and navigation', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'hv', 2, 0);

    await expect(page.locator('.mc-opt')).toHaveCount(3);
    await expect(page.locator('#exercise-content')).toContainText('Durchsage 1 von');
  });

  test('perfect score with correct answers', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'hv', 2, 0);

    const annCount = await page.evaluate(() => currentExercise.announcements.length);

    for (let i = 0; i < annCount; i++) {
      const answer = await page.evaluate((idx) => currentExercise.announcements[idx].answer, i);
      await page.locator(`#hv2-opt-${answer}`).click();

      if (i < annCount - 1) {
        await page.locator('button', { hasText: 'Nächste' }).click();
      }
    }

    await page.locator('button', { hasText: 'Auswerten' }).click();
    await expect(page.locator('#results-title')).toContainText('Perfekt');
  });
});

// ─── HV Teil 3: Interview ───────────────────────────────────────────────────

test.describe('HV Teil 3 – Interview', () => {
  test('renders statements with R/F buttons (no text-box)', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'hv', 3, 0);

    // HV3 has no text-box (audio only), but has statements
    expect(await page.locator('.statement-item').count()).toBeGreaterThan(0);
  });

  test('perfect score with correct answers', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'hv', 3, 0);

    const statements = await page.evaluate(() =>
      currentExercise.statements.map(s => ({ id: s.id, answer: s.answer }))
    );

    for (const s of statements) {
      await page.locator(`#tf-${s.id}-${s.answer ? 'r' : 'f'}`).click();
    }

    await page.locator('button', { hasText: 'Auswerten' }).click();
    await expect(page.locator('#results-title')).toContainText('Perfekt');
  });
});

// ─── HV Teil 4: Bericht ────────────────────────────────────────────────────

test.describe('HV Teil 4 – Bericht', () => {
  test('renders MC questions (no text-box)', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'hv', 4, 0);

    expect(await page.locator('.mc-item').count()).toBeGreaterThan(0);
  });

  test('perfect score with correct answers', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'hv', 4, 0);

    const questions = await page.evaluate(() =>
      currentExercise.questions.map(q => ({ id: q.id, answer: q.answer }))
    );

    for (const q of questions) {
      await page.locator(`#mc-${q.id}-${q.answer}`).click();
    }

    await page.locator('button', { hasText: 'Auswerten' }).click();
    await expect(page.locator('#results-title')).toContainText('Perfekt');
  });
});

// ─── HV Teil 5: Zuordnung ──────────────────────────────────────────────────

test.describe('HV Teil 5 – Zuordnung', () => {
  test('renders headings and scene navigation', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'hv', 5, 0);

    expect(await page.locator('.heading-card').count()).toBeGreaterThan(0);
    await expect(page.locator('#exercise-content')).toContainText('Text 1 von');
  });

  test('perfect score with correct matches', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'hv', 5, 0);

    const sceneCount = await page.evaluate(() => currentExercise.scenes.length);

    for (let i = 0; i < sceneCount; i++) {
      const answer = await page.evaluate((idx) => currentExercise.scenes[idx].answer, i);
      await page.locator(`#hv5-hd-${answer}`).click();

      if (i < sceneCount - 1) {
        await page.locator('button', { hasText: 'Nächster' }).click();
      }
    }

    await page.locator('button', { hasText: 'Auswerten' }).click();
    await expect(page.locator('#results-title')).toContainText('Perfekt');
  });
});

// ─── Score persistence (localStorage) ───────────────────────────────────────

test.describe('Score persistence', () => {
  test('score is saved to localStorage after completing an exercise', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 3, 0);

    // Answer all correctly
    const statements = await page.evaluate(() =>
      currentExercise.statements.map(s => ({ id: s.id, answer: s.answer }))
    );
    for (const s of statements) {
      await page.locator(`#tf-${s.id}-${s.answer ? 'r' : 'f'}`).click();
    }
    await page.locator('button', { hasText: 'Auswerten' }).click();
    await expect(page.locator('#results-panel')).toBeVisible();

    // Check localStorage
    const stored = await page.evaluate(() => {
      const data = JSON.parse(localStorage.getItem('dsd-exercise-scores') || '{}');
      return data;
    });
    const exerciseId = await page.evaluate(() => currentExercise.id);
    expect(stored[exerciseId]).toBeDefined();
    expect(stored[exerciseId].pct).toBe(100);
  });

  test('score badge appears on exercise list after completing', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 3, 0);

    // Get exercise id for verification
    const exerciseId = await page.evaluate(() => currentExercise.id);

    // Answer all correctly
    const statements = await page.evaluate(() =>
      currentExercise.statements.map(s => ({ id: s.id, answer: s.answer }))
    );
    for (const s of statements) {
      await page.locator(`#tf-${s.id}-${s.answer ? 'r' : 'f'}`).click();
    }
    await page.locator('button', { hasText: 'Auswerten' }).click();
    await expect(page.locator('#results-panel')).toBeVisible();

    // Go home and navigate back to exercise list
    await page.locator('button', { hasText: 'Hauptmenü' }).click();
    await page.locator('.section-card').first().click();
    await page.locator('#part-screen .part-card').nth(2).click(); // Teil 3

    // The first exercise should show a perfect badge
    const badge = page.locator('#set-list .part-card').first().locator('.score-badge');
    await expect(badge).toBeVisible();
    await expect(badge).toContainText('100%');
    await expect(badge).toHaveClass(/score-badge-perfect/);
  });

  test('part card shows progress summary after completing', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 3, 0);

    // Answer all correctly
    const statements = await page.evaluate(() =>
      currentExercise.statements.map(s => ({ id: s.id, answer: s.answer }))
    );
    for (const s of statements) {
      await page.locator(`#tf-${s.id}-${s.answer ? 'r' : 'f'}`).click();
    }
    await page.locator('button', { hasText: 'Auswerten' }).click();

    // Go home → LV parts
    await page.locator('button', { hasText: 'Hauptmenü' }).click();
    await page.locator('.section-card').first().click();

    // Teil 3 card should show progress
    const teil3Card = page.locator('#part-screen .part-card').nth(2);
    const summary = teil3Card.locator('.progress-summary');
    await expect(summary).toBeVisible();
    await expect(summary).toContainText('1 perfekt');
    await expect(summary).toContainText('versucht');
  });

  test('start screen shows section progress summary', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 3, 0);

    // Answer all correctly
    const statements = await page.evaluate(() =>
      currentExercise.statements.map(s => ({ id: s.id, answer: s.answer }))
    );
    for (const s of statements) {
      await page.locator(`#tf-${s.id}-${s.answer ? 'r' : 'f'}`).click();
    }
    await page.locator('button', { hasText: 'Auswerten' }).click();

    // Go home
    await page.locator('button', { hasText: 'Hauptmenü' }).click();

    // LV section card should show progress
    const lvCard = page.locator('.section-card').first();
    const summary = lvCard.locator('.progress-summary');
    await expect(summary).toBeVisible();
    await expect(summary).toContainText('1 perfekt');
  });

  test('best score is kept (worse score does not overwrite)', async ({ page }) => {
    await openApp(page);

    // First: get a perfect score
    await startExercise(page, 'lv', 3, 0);
    const statements = await page.evaluate(() =>
      currentExercise.statements.map(s => ({ id: s.id, answer: s.answer }))
    );
    for (const s of statements) {
      await page.locator(`#tf-${s.id}-${s.answer ? 'r' : 'f'}`).click();
    }
    await page.locator('button', { hasText: 'Auswerten' }).click();
    await expect(page.locator('#results-panel')).toBeVisible();

    // Second: retry with all wrong answers
    await page.locator('#results-retry-btn').click();
    await expect(page.locator('#exercise-content')).toBeVisible();

    const statements2 = await page.evaluate(() =>
      currentExercise.statements.map(s => ({ id: s.id, answer: s.answer }))
    );
    for (const s of statements2) {
      await page.locator(`#tf-${s.id}-${s.answer ? 'f' : 'r'}`).click(); // wrong
    }
    await page.locator('button', { hasText: 'Auswerten' }).click();

    // Verify localStorage still has 100%
    const stored = await page.evaluate(() => {
      const data = JSON.parse(localStorage.getItem('dsd-exercise-scores') || '{}');
      return data;
    });
    const exerciseId = await page.evaluate(() => currentExercise.id);
    expect(stored[exerciseId].pct).toBe(100);
  });
});

// ─── Timer ──────────────────────────────────────────────────────────────────

test.describe('Timer', () => {
  test('timer starts when exercise begins and stops on submit', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 3, 0);

    // Timer should be visible and running
    await expect(page.locator('#timer-wrap')).toBeVisible();

    // Wait a moment then check timer has advanced
    await page.waitForTimeout(1500);
    const timeText = await page.locator('#timer-display').textContent();
    expect(timeText).not.toBe('0:00');

    // Submit and check timer is in results
    const statements = await page.evaluate(() =>
      currentExercise.statements.map(s => ({ id: s.id, answer: s.answer }))
    );
    for (const s of statements) {
      await page.locator(`#tf-${s.id}-${s.answer ? 'r' : 'f'}`).click();
    }
    await page.locator('button', { hasText: 'Auswerten' }).click();

    await expect(page.locator('#results-detail')).toContainText('Zeit:');
  });
});

// ─── Results display ────────────────────────────────────────────────────────

test.describe('Results display', () => {
  test('shows correct emoji based on score percentage', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 3, 0);

    // Answer all wrong → "Weiter üben"
    const statements = await page.evaluate(() =>
      currentExercise.statements.map(s => ({ id: s.id, answer: s.answer }))
    );
    for (const s of statements) {
      await page.locator(`#tf-${s.id}-${s.answer ? 'f' : 'r'}`).click();
    }
    await page.locator('button', { hasText: 'Auswerten' }).click();

    await expect(page.locator('#results-title')).toContainText('Weiter üben');
  });

  test('retry button restarts the same exercise', async ({ page }) => {
    await openApp(page);
    await startExercise(page, 'lv', 3, 0);

    const statements = await page.evaluate(() =>
      currentExercise.statements.map(s => ({ id: s.id, answer: s.answer }))
    );
    for (const s of statements) {
      await page.locator(`#tf-${s.id}-${s.answer ? 'r' : 'f'}`).click();
    }
    await page.locator('button', { hasText: 'Auswerten' }).click();
    await expect(page.locator('#results-panel')).toBeVisible();

    // Click retry
    await page.locator('#results-retry-btn').click();
    await expect(page.locator('#exercise-content')).toBeVisible();
    // Should be back in the exercise with fresh state
    expect(await page.locator('.statement-item').count()).toBeGreaterThan(0);
    // No tf-selected buttons (fresh state)
    await expect(page.locator('.tf-selected')).toHaveCount(0);
  });
});

// ─── Data integrity: all exercises load without errors ──────────────────────

test.describe('Data integrity', () => {
  test('all LV exercises can be opened without JS errors', async ({ page }) => {
    const errors = [];
    page.on('pageerror', err => errors.push(err.message));

    await openApp(page);

    for (let teil = 1; teil <= 5; teil++) {
      const setCount = await page.evaluate((t) => {
        const key = 'teil' + t;
        return DATA.leseverstehen[key].length;
      }, teil);

      for (let i = 0; i < setCount; i++) {
        await startExercise(page, 'lv', teil, i);
        // Verify exercise content rendered
        const content = await page.locator('#exercise-content').innerHTML();
        expect(content.length).toBeGreaterThan(0);
        // Go back to start for next iteration
        await page.locator('button', { hasText: 'Beenden' }).click();
      }
    }

    expect(errors).toEqual([]);
  });

  test('all HV exercises can be opened without JS errors', async ({ page }) => {
    const errors = [];
    page.on('pageerror', err => errors.push(err.message));

    await openApp(page);

    for (let teil = 1; teil <= 5; teil++) {
      const setCount = await page.evaluate((t) => {
        const key = 'teil' + t;
        return DATA.hoerverstehen[key].length;
      }, teil);

      for (let i = 0; i < setCount; i++) {
        await startExercise(page, 'hv', teil, i);
        const content = await page.locator('#exercise-content').innerHTML();
        expect(content.length).toBeGreaterThan(0);
        await page.locator('button', { hasText: 'Beenden' }).click();
      }
    }

    expect(errors).toEqual([]);
  });
});

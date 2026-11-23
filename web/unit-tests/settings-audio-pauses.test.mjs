import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { Buffer } from 'node:buffer';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

// settings-fields.ts imports the language registry JSON, so bundle it.
const built = await build({
  entryPoints: [
    fileURLToPath(new URL('../src/lib/settings-fields.ts', import.meta.url))
  ],
  bundle: true,
  write: false,
  format: 'esm',
  loader: { '.json': 'json' }
});
const { compareSettingOrder, settingLabel, settingApplies } = await import(
  `data:text/javascript;base64,${Buffer.from(built.outputFiles[0].text).toString('base64')}`
);

const PAUSES = [
  'sentence_silence_ms',
  'paragraph_silence_ms',
  'clause_silence_ms',
  'in_segment_voice_change_silence_ms',
  'voice_change_silence_ms',
  'chapter_silence_ms',
  'chapter_title_silence_ms'
];

test('the chapter pauses follow the existing pauses in the audio section order', () => {
  const shuffled = [
    'fade_enabled',
    'chapter_title_silence_ms',
    ...[...PAUSES]
      .reverse()
      .filter((key) => key !== 'chapter_title_silence_ms'),
    'audio_verification_mode'
  ];
  const ordered = shuffled.sort((left, right) =>
    compareSettingOrder('audio', left, right)
  );
  assert.deepEqual(ordered, [
    'audio_verification_mode',
    ...PAUSES,
    'fade_enabled'
  ]);
});

test('the chapter pauses have their labels and apply with default settings', () => {
  assert.equal(
    settingLabel('chapter_silence_ms'),
    'Pause before a chapter heading (ms)'
  );
  assert.equal(
    settingLabel('chapter_title_silence_ms'),
    'Pause after a chapter title (ms)'
  );
  for (const key of ['chapter_silence_ms', 'chapter_title_silence_ms'])
    assert.equal(settingApplies('audio', key, {}), true);
});

test("the Settings panel's audio list shows the chapter pauses after voice changes", () => {
  const source = readFileSync(
    new URL('../src/lib/SettingsPanel.svelte', import.meta.url),
    'utf8'
  );
  const list = source.match(/\baudio:\s*\[([^\]]*)\]/);
  assert.ok(list, 'audio list found in SettingsPanel.svelte');
  const keys = [...list[1].matchAll(/'([a-z_]+)'/g)].map((match) => match[1]);
  const at = keys.indexOf('voice_change_silence_ms');
  assert.ok(at >= 0);
  assert.deepEqual(keys.slice(at + 1, at + 3), [
    'chapter_silence_ms',
    'chapter_title_silence_ms'
  ]);
});

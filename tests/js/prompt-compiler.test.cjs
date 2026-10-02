/**
 * Unit tests for H3 Studio Lab Prompt Compiler.
 */

const { test, describe } = require('node:test');
const assert = require('node:assert/strict');
const { compilePrompt, parseStructuredSections } = require('../../web/h3/prompt-compiler.js');

describe('H3 Prompt Compiler', () => {
  test('custom role compiles neutral picture tag without forced preservation', () => {
    const compiled = compilePrompt({
      mode: 'refs',
      prompt_mode: 'guided',
      source_prompt: 'Use @lighting for light direction only; replace the person.',
      references: [
        { asset_id: 'a', alias: 'lighting', kind: 'image', role: 'custom' }
      ]
    });

    assert.ok(compiled.compiled_prompt.includes('<Picture 1>'), 'Should include <Picture 1>');
    assert.ok(!compiled.compiled_prompt.includes('fully_preserved'), 'Custom role must not force fully_preserved');
    assert.ok(!compiled.compiled_prompt.includes('character identity'), 'Custom role must not invent character identity');
    assert.ok(!compiled.compiled_prompt.includes('<Subject 1>'), 'Custom image role must not generate <Subject 1>');
    assert.equal(compiled.bindings.length, 1);
    assert.equal(compiled.bindings[0].tag, '<Picture 1>');
  });

  test('mixed character, custom, and storyboard images maintain stable tags', () => {
    const compiled = compilePrompt({
      mode: 'refs',
      prompt_mode: 'guided',
      source_prompt: 'Scene with @hero guided by @board and illuminated by @neon.',
      references: [
        { asset_id: '1', alias: 'hero', kind: 'image', role: 'character identity' },
        { asset_id: '2', alias: 'board', kind: 'image', role: 'storyboard' },
        { asset_id: '3', alias: 'neon', kind: 'image', role: 'custom', instruction: 'intense pink neon backlight' }
      ]
    });

    // hero -> Picture 1, Subject 1
    // board -> Picture 2
    // neon -> Picture 3
    assert.ok(compiled.compiled_prompt.includes('<Subject 1>'), 'Hero gets Subject 1');
    assert.ok(compiled.compiled_prompt.includes('<Picture 2>'), 'Board maps to Picture 2');
    assert.ok(compiled.compiled_prompt.includes('<Picture 3>'), 'Neon maps to Picture 3');
    assert.ok(!compiled.compiled_prompt.includes('<Subject 2>'), 'Storyboard and custom must not increment Subject index');
    assert.ok(compiled.compiled_prompt.includes('intense pink neon backlight'), 'Custom instruction preserved in summary');
  });

  test('structured prompt is validated and passed through without second wrapping', () => {
    const rawStructured = [
      'subject_definitions:',
      'No separate still-image subject is defined.',
      '',
      'summary:',
      '[reference generation] Hero enters neon room <Picture 1>',
      '',
      'retention_analysis:',
      '<Picture 1>: attribute_transfer - lighting only.',
      '',
      'detailed_description:',
      '[Shot 1] Action begins with @lighting as key light.',
      '',
      'overall_soundscape:',
      'Hum of neon transformers.',
      '',
      'non_diegetic_music:',
      'Synthwave bassline.'
    ].join('\n');

    const compiled = compilePrompt({
      mode: 'refs',
      prompt_mode: 'structured',
      source_prompt: rawStructured,
      references: [
        { asset_id: 'x', alias: 'lighting', kind: 'image', role: 'custom' }
      ]
    });

    // Check occurrences of sections: exactly one occurrence of each section!
    const matches = compiled.compiled_prompt.match(/subject_definitions:/g);
    assert.equal(matches.length, 1, 'Should have exactly one subject_definitions section');
    const descMatches = compiled.compiled_prompt.match(/detailed_description:/g);
    assert.equal(descMatches.length, 1, 'Should have exactly one detailed_description section');

    // Mentions inside structured prompt substituted
    assert.ok(compiled.compiled_prompt.includes('<Picture 1> as key light'));
    assert.ok(!compiled.compiled_prompt.includes('@lighting'));
  });

  test('structured prompt referencing nonexistent Picture 4 raises actionable error', () => {
    const structuredWithBadIndex = [
      'subject_definitions:',
      'None',
      '',
      'summary:',
      'Test summary referencing <Picture 4>',
      '',
      'retention_analysis:',
      'None',
      '',
      'detailed_description:',
      '[Shot 1] Look at @ref1.',
      '',
      'overall_soundscape:',
      'Silence',
      '',
      'non_diegetic_music:',
      'None'
    ].join('\n');

    assert.throws(() => {
      compilePrompt({
        mode: 'refs',
        prompt_mode: 'structured',
        source_prompt: structuredWithBadIndex,
        references: [
          { asset_id: '1', alias: 'ref1', kind: 'image', role: 'custom' }
        ]
      });
    }, /Referenced <Picture 4> does not exist/);
  });

  test('duplicate structured sections raise actionable error', () => {
    const dupSections = [
      'subject_definitions:',
      'None',
      'subject_definitions:',
      'Duplicate!',
      'summary:',
      'Test',
      'detailed_description:',
      '[Shot 1] Action with @a',
      'overall_soundscape:',
      'Ambiance',
      'non_diegetic_music:',
      'None'
    ].join('\n');

    assert.throws(() => {
      compilePrompt({
        mode: 'refs',
        source_prompt: dupSections,
        references: [{ asset_id: '1', alias: 'a', kind: 'image', role: 'custom' }]
      });
    }, /Duplicate section\(s\)/);
  });

  test('reference limits: 12 files, 9 images, 3 audios', () => {
    // 10 images -> rejected
    const tenImages = Array.from({ length: 10 }, (_, i) => ({
      asset_id: `img_${i}`,
      alias: `img${i}`,
      kind: 'image',
      role: 'custom'
    }));

    assert.throws(() => {
      compilePrompt({
        mode: 'refs',
        source_prompt: 'test',
        references: tenImages
      });
    }, /at most 9 reference images/);

    // 4 audio references (2 standalone + 2 video with soundtrack) -> rejected
    const excessAudio = [
      { asset_id: 'a1', alias: 'a1', kind: 'audio', role: 'music' },
      { asset_id: 'a2', alias: 'a2', kind: 'audio', role: 'sound effects' },
      { asset_id: 'v1', alias: 'v1', kind: 'video', role: 'motion', use_audio: true },
      { asset_id: 'v2', alias: 'v2', kind: 'video', role: 'motion', use_audio: true },
    ];

    assert.throws(() => {
      compilePrompt({
        mode: 'refs',
        source_prompt: 'test',
        references: excessAudio
      });
    }, /at most three audio references/);
  });

  test('unmentioned reference raises actionable error', () => {
    assert.throws(() => {
      compilePrompt({
        mode: 'refs',
        source_prompt: 'Only mentioning @hero here.',
        references: [
          { asset_id: '1', alias: 'hero', kind: 'image', role: 'character identity' },
          { asset_id: '2', alias: 'villain', kind: 'image', role: 'character identity' }
        ]
      });
    }, /Mention every attached reference in the prompt: @villain/);
  });

  test('frames mode start and end mentions compile to Picture 1 and 2', () => {
    const compiled = compilePrompt({
      mode: 'frames',
      source_prompt: 'Transition from @start to @end smoothly.',
      frames: { first: 'first.png', last: 'last.png' },
      target_seconds: 5.17
    });

    assert.ok(compiled.compiled_prompt.includes('Begin with <Picture 1> and end with <Picture 2>.'));
    assert.ok(compiled.compiled_prompt.includes('Transition from <Picture 1> to <Picture 2> smoothly.'));
    assert.equal(compiled.bindings.length, 2);
  });
});

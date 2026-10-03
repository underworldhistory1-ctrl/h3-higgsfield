/**
 * H3 Studio Lab — Deterministic Reference & Prompt Compiler
 *
 * Compiles user prompts and reference definitions into native MiniMax H3 tags
 * (<Picture N>, <Subject N>, <Video N>, <Audio N>) and structural sections.
 *
 * Key guarantees:
 * - Deterministic, pure function without DOM coupling.
 * - 'Custom' role compiles neutral media tags without forcing identity preservation.
 * - Structured prompts are parsed, validated, and never wrapped twice.
 * - Numerical tags remain stable and separate picture vs subject indexing.
 * - Preserves dialogue, timing, and music decisions verbatim.
 */

(function(root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.H3PromptCompiler = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  'use strict';

  const NATIVE_REF_SECTIONS = [
    'subject_definitions',
    'summary',
    'retention_analysis',
    'detailed_description',
    'overall_soundscape',
    'non_diegetic_music',
  ];

  const NATIVE_TEXT_SECTIONS = [
    'integrated_multimodal_description',
    'overall_soundscape',
    'non_diegetic_music',
  ];

  function validateLimits(references = []) {
    if (references.length > 12) {
      throw new Error('H3 accepts at most 12 reference files.');
    }
    if (references.some(r => !['image', 'video', 'audio'].includes(r.kind))) throw new Error('Unknown reference kind.');
    const images = references.filter(r => r.kind === 'image');
    if (images.length > 9) {
      throw new Error('H3 accepts at most 9 reference images.');
    }
    const videos = references.filter(r => r.kind === 'video');
    if (videos.length > 3) {
      throw new Error('H3 accepts at most 3 reference videos.');
    }
    const audios = references.filter(r => r.kind === 'audio' || (r.kind === 'video' && r.use_audio));
    if (audios.length > 3) {
      throw new Error('H3 accepts at most three audio references, including video soundtracks.');
    }
    const totalVideoSecs = videos.reduce((acc, v) => acc + (Number(v.duration || v.local_duration) || 0), 0);
    if (totalVideoSecs > 15.1) {
      throw new Error(`Video references total ${totalVideoSecs.toFixed(2)} seconds. H3 allows 15 seconds combined; trim and retry.`);
    }
  }

  function parseStructuredSections(prompt) {
    const sectionRegex = /^([a-z_]+):\s*/gim;
    const matches = [...prompt.matchAll(sectionRegex)];
    if (!matches.length) return null;

    const sections = {};
    const seen = new Set();
    const duplicates = [];

    for (let i = 0; i < matches.length; i++) {
      const name = matches[i][1].toLowerCase();
      if (seen.has(name)) {
        duplicates.push(name);
      }
      seen.add(name);
      const startIdx = matches[i].index + matches[i][0].length;
      const endIdx = i + 1 < matches.length ? matches[i + 1].index : prompt.length;
      sections[name] = prompt.slice(startIdx, endIdx).trim();
    }

    if (duplicates.length) {
      throw new Error(`Duplicate section(s) in structured prompt: ${[...new Set(duplicates)].join(', ')}`);
    }
    return sections;
  }

  function orderedReferences(references = []) {
    const images = references.filter(r => r.kind === 'image');
    const videos = references.filter(r => r.kind === 'video');
    const audios = references.filter(r => r.kind === 'audio');
    return [...images, ...videos, ...audios];
  }

  function validateSubjectBindings(prompt, sections = parseStructuredSections(prompt), bindings = []) {
    const used = [...prompt.matchAll(/<Subject\s+(\d+)>/gi)].map(match => Number(match[1]));
    const definitions = sections?.subject_definitions || '';
    const imageCount = bindings.filter(binding => binding.kind === 'image').length;
    for (const index of new Set(used)) {
      const lines = definitions.split(/\n/).filter(line => new RegExp(`<Subject\\s+${index}>`, 'i').test(line));
      const pictures = lines.flatMap(line => [...line.matchAll(/<Picture\s+(\d+)>/gi)].map(match => Number(match[1])));
      if (index < 1 || !pictures.length || pictures.some(picture => picture < 1 || picture > imageCount)) {
        throw new Error(`Subject ${index} must be defined with a connected Picture in subject_definitions.`);
      }
      const bound = bindings.find(binding => binding.subject_idx === index);
      if (bound && !pictures.includes(bound.picture_idx)) throw new Error(`Subject ${index} definition conflicts with its attached image binding.`);
    }
  }

  function compilePrompt(spec) {
    const mode = spec.mode || 'text';
    const promptMode = spec.prompt_mode || 'guided';
    let source = (spec.source_prompt || '').trim();
    const warnings = [];

    if (!source) {
      throw new Error('Write the scene prompt first.');
    }

    const expectedSections = mode === 'refs' ? NATIVE_REF_SECTIONS : NATIVE_TEXT_SECTIONS;
    const nativeNames = [...new Set([...NATIVE_REF_SECTIONS, ...NATIVE_TEXT_SECTIONS])];
    const hasNativeSections = new RegExp('^(?:' + nativeNames.join('|') + '):\\s*', 'im').test(source);
    // Ordinary prose headings (including repeated Action/Camera headings) are content.
    const originalSections = promptMode === 'structured' || hasNativeSections ? parseStructuredSections(source) : null;
    if (promptMode === 'structured' || hasNativeSections) {
      if (!originalSections) throw new Error('Structured prompt requires native section headers.');
      const missing = expectedSections.filter(name => !originalSections[name]);
      if (missing.length) throw new Error('Structured prompt is missing native section(s): ' + missing.join(', '));
      const unknown = Object.keys(originalSections).filter(name => !expectedSections.includes(name));
      if (unknown.length) throw new Error('Unknown structured section(s): ' + unknown.join(', '));
    }

    // --- FRAMES MODE ---
    if (mode === 'frames') {
      const frames = spec.frames || {};
      if (!frames.first && !frames.last) {
        throw new Error('Add a start frame, an end frame, or both.');
      }
      const frameTags = new Map();
      const bindings = [];
      if (frames.first) {
        frameTags.set('start', '<Picture 1>');
        bindings.push({ alias: 'start', tag: '<Picture 1>', kind: 'image', slot: 'first' });
      }
      if (frames.last) {
        const endTag = frames.first ? '<Picture 2>' : '<Picture 1>';
        frameTags.set('end', endTag);
        bindings.push({ alias: 'end', tag: endTag, kind: 'image', slot: 'last' });
      }

      // Mention replacement
      source = source.replace(/@([\p{L}\p{N}_-]+)/gu, (whole, name) => {
        if (!frameTags.has(name)) {
          throw new Error(`Frame mention ${whole} has no matching uploaded frame.`);
        }
        return frameTags.get(name);
      });

      const structured = originalSections ? parseStructuredSections(source) : null;
      if (structured && structured.integrated_multimodal_description) {
        return { compiled_prompt: source, bindings, warnings };
      }

      const anchor = frames.first && frames.last ? 'Begin with <Picture 1> and end with <Picture 2>. ' :
        frames.first ? 'Begin with <Picture 1>. ' : 'End with <Picture 1>. ';

      const durationSec = Number(spec.target_seconds || 5.17).toFixed(2);
      const alignment = frames.first && frames.last ?
        `How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the ${durationSec}-second mark of the target video.` :
        frames.first ?
          `For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.` :
          `How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the ${durationSec}-second mark of the target video.`;

      const compiled = `${alignment}\n\nintegrated_multimodal_description: [Shot 1] ${anchor}${source}\n\noverall_soundscape: Use the sounds described in [Shot 1]; otherwise only natural scene ambience.\n\nnon_diegetic_music: Only music explicitly requested in [Shot 1].`;
      return { compiled_prompt: compiled, bindings, warnings };
    }

    // --- TEXT MODE ---
    if (mode === 'text') {
      const structured = originalSections ? parseStructuredSections(source) : null;
      if (structured && structured.integrated_multimodal_description) {
        return { compiled_prompt: source, bindings: [], warnings };
      }
      const compiled = `integrated_multimodal_description: [Shot 1] ${source}\n\noverall_soundscape: Use the sounds described in [Shot 1]; otherwise only natural scene ambience.\n\nnon_diegetic_music: Only music explicitly requested in [Shot 1].`;
      return { compiled_prompt: compiled, bindings: [], warnings };
    }

    // --- REFERENCES MODE ---
    if (mode === 'refs') {
      const rawRefs = spec.references || [];
      if (!rawRefs.length) {
        throw new Error('Add at least one reference.');
      }
      validateLimits(rawRefs);

      // Check alias uniqueness
      const aliasSet = new Set();
      for (const r of rawRefs) {
        if (!r.alias || !/^[\p{L}\p{N}_-]{1,32}$/u.test(r.alias)) {
          throw new Error(`Invalid reference alias: '${r.alias}'. Use 1-32 letters, numbers, _ or -.`);
        }
        if (aliasSet.has(r.alias)) {
          throw new Error(`Duplicate reference alias: @${r.alias}`);
        }
        aliasSet.add(r.alias);
      }

      const ordered = orderedReferences(rawRefs);
      const bindings = [];
      const aliasToTag = new Map();
      const definitions = [];
      const retention = [];
      const mediaNotes = [];

      let imageIdx = 0;
      let subjectIdx = 0;
      let videoIdx = 0;
      let audioIdx = 0;

      for (const ref of ordered) {
        const role = (ref.role || 'custom').toLowerCase().trim();
        let tag;
        let bindingInfo = {
          asset_id: ref.asset_id,
          alias: ref.alias,
          kind: ref.kind,
          role: role,
        };

        if (ref.kind === 'image') {
          const picNum = ++imageIdx;
          const picTag = `<Picture ${picNum}>`;
          bindingInfo.picture_idx = picNum;

          if (role === 'custom') {
            tag = picTag;
            bindingInfo.tag = tag;
            if (ref.instruction) {
              mediaNotes.push(`${picTag}: ${ref.instruction}`);
            }
          } else if (role === 'storyboard') {
            tag = picTag;
            bindingInfo.tag = tag;
            mediaNotes.push(`${picTag} is a multi-panel storyboard. ${ref.panel_order || "Use the panel/shot order described in the prompt"} as a shot and composition guide; do not render panel borders or labels. Follow explicit shot descriptions when they differ. ${ref.instruction || ""}`);
            retention.push(`${picTag}: attribute_transfer - guide shot order, framing and visual progression; do not treat the sheet as a single visible subject.`);
          } else {
            const subNum = ++subjectIdx;
            tag = `<Subject ${subNum}>`;
            bindingInfo.tag = tag;
            bindingInfo.subject_idx = subNum;
            definitions.push(`${tag} is the ${role} shown in ${picTag}. Keep its visible defining details.`);
            const retainMode = role === 'visual style' ? 'attribute_transfer' : 'fully_preserved';
            retention.push(`${tag}: ${retainMode} - preserve the defining visual attributes shown in ${picTag} wherever this subject appears in the target sequence.`);
          }
        } else if (ref.kind === 'video') {
          const vidNum = ++videoIdx;
          tag = `<Video ${vidNum}>`;
          bindingInfo.video_idx = vidNum;
          bindingInfo.tag = tag;

          if (ref.use_audio) {
            const audNum = ++audioIdx;
            const audioTag = `<Audio ${audNum}>`;
            bindingInfo.paired_audio_tag = audioTag;
            bindingInfo.paired_audio_idx = audNum;
            mediaNotes.push(`${audioTag} is the soundtrack paired with reference video ${tag}.`);
          }

          if (role === 'whole scene') {
            mediaNotes.push(`${tag} is a whole-scene reference. Follow its action, camera movement, composition and timing. Follow its subjects and setting only where the detailed_description does not replace them. Explicit changes in the detailed_description take priority. Generate a new video rather than treating reference frames as locked pixels.`);
          } else if (role === 'motion and camera') {
            mediaNotes.push(`${tag} guides body performance, action timing and camera movement only. Do not transfer its actor identity, wardrobe, voice or location unless the detailed_description explicitly requests them.`);
          } else if (role === 'custom') {
            if (ref.instruction) {
              mediaNotes.push(`${tag}: ${ref.instruction}`);
            }
          } else {
            mediaNotes.push(`${tag} is a ${role} reference only. Do not transfer its actor identity, wardrobe, voice or location unless the detailed_description explicitly requests them.`);
          }
        } else if (ref.kind === 'audio') {
          const audNum = ++audioIdx;
          tag = `<Audio ${audNum}>`;
          bindingInfo.audio_idx = audNum;
          bindingInfo.tag = tag;
          if (role === 'custom' && ref.instruction) {
            mediaNotes.push(`${tag}: ${ref.instruction}`);
          } else {
            mediaNotes.push(`${tag} is a ${role} reference.`);
          }
        }

        aliasToTag.set(ref.alias, tag);
        bindings.push(bindingInfo);
      }

      // Native structured tags are valid reference mentions as well as aliases.
      const nativeSections = originalSections;
      const missing = bindings.filter(binding => {
        const pattern = new RegExp('@' + binding.alias + '(?=$|[^\\p{L}\\p{N}_-])', 'u');
        if (pattern.test(source)) return false;
        if (!nativeSections) return true;
        const tags = [binding.tag];
        if (binding.picture_idx) tags.push(`<Picture ${binding.picture_idx}>`);
        return !tags.some(tag => source.includes(tag));
      });
      if (missing.length) throw new Error('Mention every attached reference in the prompt: ' + missing.map(r => '@' + r.alias).join(', '));

      // Substitute mentions
      const unknown = [];
      const substituted = source.replace(/@([\p{L}\p{N}_-]+)/gu, (whole, name) => {
        if (!aliasToTag.has(name)) {
          unknown.push(whole);
          return whole;
        }
        return aliasToTag.get(name);
      });
      if (unknown.length) {
        throw new Error('Unknown mention: ' + [...new Set(unknown)].join(', '));
      }

      // Check if prompt is already structured or user chose structured mode
      const structured = originalSections ? parseStructuredSections(substituted) : null;
      if (structured && (promptMode === 'structured' || hasNativeSections)) {
        validateSubjectBindings(substituted, structured, bindings);
        // Validate native token indices in structured prompt
        const picTokens = [...substituted.matchAll(/<Picture\s+(\d+)>/gi)];
        for (const m of picTokens) {
          const idx = parseInt(m[1], 10);
          if (idx < 1 || idx > imageIdx) {
            throw new Error(`Referenced <Picture ${idx}> does not exist. Connected images: ${imageIdx}`);
          }
        }
        const vidTokens = [...substituted.matchAll(/<Video\s+(\d+)>/gi)];
        for (const m of vidTokens) {
          const idx = parseInt(m[1], 10);
          if (idx < 1 || idx > videoIdx) {
            throw new Error(`Referenced <Video ${idx}> does not exist. Connected videos: ${videoIdx}`);
          }
        }
        const audTokens = [...substituted.matchAll(/<Audio\s+(\d+)>/gi)];
        for (const m of audTokens) {
          const idx = parseInt(m[1], 10);
          if (idx < 1 || idx > audioIdx) {
            throw new Error(`Referenced <Audio ${idx}> does not exist. Connected audios: ${audioIdx}`);
          }
        }

        // Return structured prompt without adding another wrapper
        return { compiled_prompt: substituted, bindings, warnings };
      }

      // Guided mode: wrap deterministically into native schema
      const shotOne = /\[Shot\s+1\]/i.test(substituted);
      const summaryLead = (substituted.match(/^[^\n.!?]+[.!?]?/) || [])[0]?.trim() || 'Generate the requested target sequence.';

      const compiled = [
        'subject_definitions:',
        definitions.join('\n') || 'No separate still-image subject is defined.',
        '',
        'summary:',
        `[reference generation] ${summaryLead} ${mediaNotes.join(' ')}`.trim(),
        '',
        'retention_analysis:',
        retention.join('\n') || 'Preserve the motion and sound qualities of the cited references.',
        '',
        'detailed_description:',
        shotOne ? substituted : `[Shot 1] ${substituted}`,
        '',
        'overall_soundscape:',
        'Use cited audio references and scene sounds described in the detailed_description, timed to their shots.',
        '',
        'non_diegetic_music:',
        'Only music explicitly requested in the detailed_description.',
      ].join('\n');

      return { compiled_prompt: compiled, bindings, warnings };
    }

    throw new Error(`Unknown mode: ${mode}`);
  }

  return {
    compilePrompt,
    parseStructuredSections,
    orderedReferences,
    validateLimits,
    validateSubjectBindings,
  };
});

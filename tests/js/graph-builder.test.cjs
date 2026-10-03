/**
 * Unit tests for H3 Studio Lab Graph Builder.
 */

const { test, describe } = require('node:test');
const assert = require('node:assert/strict');
const { buildGraph } = require('../../web/h3/graph-builder.js');

describe('H3 Graph Builder', () => {
  test('frames mode: first and last frames use nodes 15 and 16, avoiding node 13 collision', () => {
    const spec = {
      mode: 'frames',
      compiled_prompt: 'Test frames prompt',
      width: 1280,
      height: 704,
      target_frames: 124,
      token: 'abcd1234ef56',
    };
    const resolvedAssets = {
      first: 'uploaded_start.png',
      last: 'uploaded_end.png',
    };

    const g = buildGraph(spec, resolvedAssets);

    // Node 13 is H3ReleaseForDecode
    assert.equal(g["13"].class_type, "H3ReleaseForDecode");
    // Node 15 is first_frame LoadImage
    assert.equal(g["15"].class_type, "LoadImage");
    assert.equal(g["15"].inputs.image, "uploaded_start.png");
    assert.deepEqual(g["6"].inputs.first_frame, ["15", 0]);

    // Node 16 is last_frame LoadImage
    assert.equal(g["16"].class_type, "LoadImage");
    assert.equal(g["16"].inputs.image, "uploaded_end.png");
    assert.deepEqual(g["6"].inputs.last_frame, ["16", 0]);

    // Node 12 is H3SaveVideo
    assert.equal(g["12"].class_type, "H3SaveVideo");
    assert.equal(g["12"].inputs.filename_prefix, "video/h3_studio_abcd1234ef56");
  });

  test('references mode: paired video soundtrack index matches video index', () => {
    const spec = {
      mode: 'refs',
      compiled_prompt: 'Refs with video audio',
      width: 1280,
      height: 704,
      target_frames: 124,
      token: '112233445566',
      references: [
        { asset_id: 'vid1', alias: 'clip1', kind: 'video', role: 'motion', use_audio: false },
        { asset_id: 'vid2', alias: 'clip2', kind: 'video', role: 'whole scene', use_audio: true },
      ],
    };
    const resolvedAssets = {
      vid1: 'clip1.mp4',
      vid2: 'clip2.mp4',
    };

    const g = buildGraph(spec, resolvedAssets);

    // Video 0 (clip1) has no audio
    assert.ok(g["6"].inputs["ref_videos.ref_video_0"], "Video 0 connected");
    assert.equal(g["6"].inputs["ref_video_audios.ref_video_audio_0"], undefined, "Video 0 soundtrack should not be connected");

    // Video 1 (clip2) has use_audio -> soundtrack on ref_video_audios.ref_video_audio_1
    assert.ok(g["6"].inputs["ref_videos.ref_video_1"], "Video 1 connected");
    const audioLink = g["6"].inputs["ref_video_audios.ref_video_audio_1"];
    assert.ok(audioLink, "Video 1 soundtrack must be connected at index 1");
    // Link must point to GetVideoComponents audio output (slot 1)
    const splitNodeId = audioLink[0];
    assert.equal(g[splitNodeId].class_type, "GetVideoComponents");
    assert.equal(audioLink[1], 1, "Audio slot is output 1 of GetVideoComponents");
  });

  test('temporal guides chain MiniMaxH3AddGuide positive conditioning to sampler', () => {
    const spec = {
      mode: 'refs',
      compiled_prompt: 'Refs with guides',
      width: 1280,
      height: 704,
      target_frames: 175,
      token: 'guide1234567',
      references: [
        { asset_id: 'img1', alias: 'char', kind: 'image', role: 'character identity' }
      ],
      guides: [
        { asset_id: 'start_guide', frame_idx: 0, filename: 'g_start.png' },
        { asset_id: 'mid_guide', frame_idx: 88, filename: 'g_mid.png' },
      ]
    };
    const resolvedAssets = {
      img1: 'char.png',
      start_guide: 'g_start.png',
      mid_guide: 'g_mid.png',
    };

    const g = buildGraph(spec, resolvedAssets);

    // First guide node (70): positive from ["6", 0]
    assert.equal(g["70"].class_type, "MiniMaxH3AddGuide");
    assert.deepEqual(g["70"].inputs.positive, ["6", 0]);
    assert.equal(g["70"].inputs.frame_idx, 0);

    // Second guide node (72): positive chained from ["70", 0]
    assert.equal(g["72"].class_type, "MiniMaxH3AddGuide");
    assert.deepEqual(g["72"].inputs.positive, ["70", 0]);
    assert.equal(g["72"].inputs.frame_idx, 88);

    // Sampler (node 8) positive receives the last guide in chain ["72", 0]
    assert.deepEqual(g["8"].inputs.positive, ["72", 0], "KSampler positive must receive chained guide output");
  });

  test('generated continuation links MiniMaxH3GeneratedAVMaskedContext', () => {
    const spec = {
      mode: 'text',
      compiled_prompt: 'Continuation prompt',
      width: 1280,
      height: 704,
      target_frames: 175,
      token: 'extend123456',
      continuation: {
        type: 'generated',
        source_token: 'source_latent_tok',
        context_length: 39,
        audio_feather_ticks: 8,
      }
    };

    const g = buildGraph(spec, {}, { continuation_ready: true });

    // Latent load node 86
    assert.equal(g["86"].class_type, "H3LabLoadContext");
    assert.equal(g["86"].inputs.token, "source_latent_tok");

    // Context node 85
    assert.equal(g["85"].class_type, "MiniMaxH3GeneratedAVMaskedContext");
    assert.deepEqual(g["85"].inputs.latent, ["6", 1]);
    assert.deepEqual(g["85"].inputs.source_latent, ["86", 0]);
    assert.equal(g["85"].inputs.context_length, 39);

    // Sampler latent_image comes from masked context output
    assert.deepEqual(g["8"].inputs.latent_image, ["85", 0]);
  });

  test('imported continuation links MiniMaxH3ExistingVideoMaskedContext', () => {
    const spec = {
      mode: 'text',
      compiled_prompt: 'Import continuation',
      width: 1280,
      height: 704,
      target_frames: 175,
      token: 'import123456',
      continuation: {
        type: 'imported',
        source_file: 'input_video.mp4',
        context_length: 39,
        audio_feather_ticks: 8,
      }
    };

    const g = buildGraph(spec, {}, { continuation_ready: true });

    assert.equal(g["85"].class_type, "MiniMaxH3ExistingVideoMaskedContext");
    assert.deepEqual(g["85"].inputs.latent, ["6", 1]);
    assert.deepEqual(g["85"].inputs.vae, ["4", 0]);
    assert.deepEqual(g["85"].inputs.audio_vae, ["5", 0]);
    assert.equal(g["85"].inputs.context_length, 39);
    assert.deepEqual(g["8"].inputs.latent_image, ["85", 0]);
  });
});


test('zero seed and LoRA strength survive; many guides cannot overwrite nodes', () => {
  const guides = Array.from({length: 12}, (_, i) => ({file: `guide${i}.png`, frame_idx: i}));
  const g = buildGraph({seed: 0, loras: Array.from({length: 12}, (_, i) => ({enabled: true, name: `lora${i}`, strength: 0})), guides, continuation: {type: 'generated', source_token: 'source', context_length: 39}}, {}, {continuation_ready: true});
  assert.equal(g['8'].inputs.seed, 0);
  const nodes = Object.values(g);
  assert.equal(nodes.filter(n => n.class_type === 'LoraLoaderModelOnly').length, 12);
  assert.ok(nodes.filter(n => n.class_type === 'LoraLoaderModelOnly').every(n => n.inputs.strength_model === 0));
  assert.equal(nodes.filter(n => n.class_type === 'MiniMaxH3AddGuide').length, 12);
  assert.equal(nodes.filter(n => n.class_type === 'H3LabTrimAV').length, 1);
  assert.equal(g[g['11'].inputs.images[0]].class_type, 'H3LabTrimAV');
  assert.deepEqual(g['11'].inputs.audio, [g['11'].inputs.images[0], 1]);
});

test('unresolved references and malformed guide timeline fail closed', () => {
  assert.throws(() => buildGraph({mode: 'refs', references: [{kind: 'image', asset_id: 'missing'}]}), /not been resolved/);
  for (const frame_idx of [-1, 124, 1.5, '2', NaN]) assert.throws(() => buildGraph({guides: [{file:'g.png', frame_idx}]}), /guide frame index/);
  assert.throws(() => buildGraph({continuation: {type:'generated', source_token:'s'}}), /unavailable/);
  assert.throws(() => buildGraph({continuation: {type:'unknown', source_token:'s'}}, {}, {continuation_ready:true}), /Unknown continuation/);
});

test('new-content guide offsets include protected AV context', () => {
  const g = buildGraph({guides: [{file:'g.png', frame_idx:3, relative_to_new_content:true}], continuation:{type:'imported', source_file:'source.mp4', context_length:39}}, {}, {continuation_ready:true});
  assert.equal(Object.values(g).find(n => n.class_type === 'MiniMaxH3AddGuide').inputs.frame_idx, 42);
});


test('timed audio and video guides bind actual native AV inputs', () => {
  const guides = [{kind:'audio',file:'voice.wav',frame_idx:24},{kind:'video',file:'motion.mp4',frame_idx:30,fps:24,duration_frames:48,use_audio:true}];
  const g=buildGraph({guides}, {}, {guide_audio:true,guide_video:true});
  const nodes=Object.values(g).filter(n=>n.class_type==='MiniMaxH3AddGuide');
  assert.equal(nodes.length,2);
  assert.deepEqual(nodes[0].inputs.audio_vae,['5',0]);
  assert.equal(nodes[0].inputs.image,undefined);
  assert.equal(g[nodes[0].inputs.audio[0]].class_type,'LoadAudio');
  assert.equal(g[nodes[1].inputs.image[0]].class_type,'GetVideoComponents');
  assert.deepEqual(nodes[1].inputs.audio,[nodes[1].inputs.image[0],1]);
  assert.deepEqual(nodes[1].inputs.vae,['4',0]);
  assert.deepEqual(nodes[1].inputs.audio_vae,['5',0]);
});

test('timed AV guides fail closed without capability or verified video span', () => {
  assert.throws(()=>buildGraph({guides:[{kind:'audio',file:'a.wav',frame_idx:0}]}),/audio guides are unavailable/);
  assert.throws(()=>buildGraph({guides:[{kind:'video',file:'a.mp4',frame_idx:0}]}),/video guides are unavailable/);
  assert.throws(()=>buildGraph({guides:[{kind:'video',file:'a.mp4',frame_idx:0,fps:24}]},{},{guide_video:true}),/verified frame count/);
  assert.throws(()=>buildGraph({guides:[{kind:'video',file:'a.mp4',frame_idx:100,fps:24,duration_frames:48}]},{},{guide_video:true}),/past the target timeline/);
});


test('video guide validates the native cropped 17k+5 span', () => {
  const spec={guides:[{kind:'video',file:'v.mp4',frame_idx:85,fps:24,frame_count:48}]};
  const g=buildGraph(spec,{},{guide_video:true});
  assert.equal(Object.values(g).find(n=>n.class_type==='MiniMaxH3AddGuide').inputs.frame_idx,85);
  spec.guides[0].frame_idx=86;
  assert.throws(()=>buildGraph(spec,{},{guide_video:true}),/past the target timeline/);
});

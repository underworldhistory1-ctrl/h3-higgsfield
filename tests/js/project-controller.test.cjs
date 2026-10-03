const { test } = require('node:test');
const assert = require('node:assert/strict');
const { ProjectController } = require('../../web/h3/project-controller.js');

test('asset APIs preserve multipart boundary and return canonical filename', async () => {
  const original = global.fetch;
  const requests = [];
  global.fetch = async (url, options) => {
    requests.push({url, options});
    return {ok:true, json:async () => url.endsWith('/resolve') ? {filename:'h3_lab/media.png'} : {asset:{asset_id:'asset/id'}}, blob:async () => new Blob(['media'])};
  };
  try {
    const controller = new ProjectController({apiBase:'http://localhost:8188/'});
    await controller.uploadAsset(new Blob(['data'], {type:'image/png'}), 'project');
    assert.ok(requests[0].url.endsWith('?project_id=project'));
    assert.equal(requests[0].options.headers['Content-Type'], undefined);
    assert.equal(requests[0].options.body.get('project_id'), 'project');
    assert.equal(await controller.resolveAsset('asset/id'), 'h3_lab/media.png');
    assert.ok(requests[1].url.endsWith('/assets/asset%2Fid/resolve'));
    assert.equal(await (await controller.mediaBlob('asset/id')).text(), 'media');
    await controller.getAsset('asset/id');
    assert.ok(requests[3].url.endsWith('/assets/asset%2Fid'));
  } finally { global.fetch = original; }
});

test('asset resolution fails closed on missing canonical filename', async () => {
  const original = global.fetch;
  global.fetch = async () => ({ok:true, json:async () => ({})});
  try { await assert.rejects(new ProjectController().resolveAsset('a'), /no server filename/); }
  finally { global.fetch = original; }
});

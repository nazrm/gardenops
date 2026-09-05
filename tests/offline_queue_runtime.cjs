// Run: node tests/offline_queue_runtime.cjs. Uses real Chromium IndexedDB, no backend.
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../frontend/node_modules/typescript-compiler-api');
const { chromium } = require('../frontend/node_modules/playwright-core');
const root = path.resolve(__dirname, '..');
const sources = Object.fromEntries(['services/offlineQueue', 'features/offlineFeature', 'components/offlineIndicator'].map(name => [
  name, ts.transpileModule(fs.readFileSync(path.join(root, 'frontend/src', `${name}.ts`), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText,
]));

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE || '/usr/bin/chromium', headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.route('http://queue.test/**', route => route.fulfill({ body: '<html><body></body></html>', contentType: 'text/html' }));
    await page.goto('http://queue.test/');
    const results = await page.evaluate(async sources => {
      const passed = [];
      const check = (value, message) => { if (!value) throw new Error(message); };
      let gardenId = 1;
      let online = false;
      Object.defineProperty(navigator, 'onLine', { get: () => online });
      const api = { getActiveGardenContext: () => gardenId };
      let confirmResult = true;
      const confirmations = [];
      const toasts = [];
      const modules = {
        'services/api': api,
        'core/i18n': { t: (key, params) => key + (params ? JSON.stringify(params) : '') },
        'components/toast': { showToast: message => toasts.push(message) },
        'components/dialogCore': { confirmDialog: async message => { confirmations.push(message); return confirmResult; } },
      };
      const load = name => {
        if (modules[name]) return modules[name];
        const exports = {};
        modules[name] = exports;
        new Function('exports', 'require', sources[name])(exports, dependency =>
          load(new URL(dependency, `http://modules/${name}`).pathname.slice(1)));
        return exports;
      };
      const q = load('services/offlineQueue');
      const f = load('features/offlineFeature');
      await q.initOfflineQueue();
      q.setOfflineQueueIdentity('alice');
      const reset = async () => { await q.clearOfflineQueue(); gardenId = 1; q.setOfflineQueueIdentity('alice'); };
      const file = name => new File([name], name, { type: 'image/png' });

      // Abort after the add request succeeds: request success must not acknowledge a save.
      const originalAdd = IDBObjectStore.prototype.add;
      IDBObjectStore.prototype.add = function (...args) {
        const request = originalAdd.apply(this, args);
        request.addEventListener('success', () => this.transaction.abort());
        return request;
      };
      let rejected = false;
      try { await q.enqueueDraft('journal', { title: 'abort' }); } catch { rejected = true; }
      IDBObjectStore.prototype.add = originalAdd;
      check(rejected && (await q.getAllDrafts()).length === 0, 'abort incorrectly acknowledged');
      passed.push('write resolves only after commit; abort rejects');

      IDBObjectStore.prototype.add = function (...args) {
        const request = originalAdd.apply(this, args);
        request.addEventListener('success', () => q.setOfflineQueueIdentity('bob'));
        return request;
      };
      rejected = false;
      try { await q.enqueueTaskActionBatch([{ type: 'task_complete', payload: { task_id: 'one' } }, { type: 'task_complete', payload: { task_id: 'two' } }]); } catch { rejected = true; }
      IDBObjectStore.prototype.add = originalAdd;
      q.setOfflineQueueIdentity('alice');
      check(rejected && (await q.getAllDrafts()).length === 0, 'task batch partly committed after identity change');
      passed.push('identity change during task transaction aborts the whole batch');

      for (const switchContext of [() => { gardenId = 2; }, () => q.setOfflineQueueIdentity('bob'), () => { q.setOfflineQueueIdentity(null); q.setOfflineQueueIdentity('alice'); }]) {
        await reset();
        let release;
        const delayed = file('delayed.png');
        delayed.arrayBuffer = () => new Promise(resolve => { release = resolve; });
        const pending = q.enqueueDraft('journal', { media_files: [delayed] });
        if (!release) await pending;
        switchContext();
        release(new ArrayBuffer(1));
        let rejected = false;
        try { await pending; } catch { rejected = true; }
        q.setOfflineQueueIdentity('alice');
        check(rejected && (await q.getAllDrafts()).length === 0, 'changed context committed');
      }
      passed.push('garden, identity and same-user session changes reject serialization');

      await reset();
      await q.enqueueDraft('journal', { title: 'private' });
      q.setOfflineQueueIdentity('bob');
      let calls = 0;
      await q.syncAllDrafts({ journal: async () => { calls++; } });
      check(calls === 0 && (await q.getAllDrafts()).length === 0, 'another owner replayed');
      q.setOfflineQueueIdentity('alice');
      check((await q.getAllDrafts()).length === 1, 'private work was deleted');
      passed.push('mismatched owners cannot list or replay');

      await reset();
      let creates = 0;
      let fail = true;
      const uploads = [];
      let pendingRecord = null;
      let openedRecord = null;
      api.createJournalEntryApi = async payload => {
        check(!('_serialized_media' in payload) && !('_confirmed_journal_entry_id' in payload), 'internal payload leaked');
        creates++;
        return { id: 77 };
      };
      api.uploadMediaApi = async options => {
        uploads.push([options.file.name, options.operationId]);
        if (options.file.name === 'second.png' && fail) throw Object.assign(new Error('bad image'), { status: 400 });
        return { asset_id: 9 };
      };
      api.addMediaLinkApi = async () => {};
      f.initOfflineFeature({
        extractPendingMediaFiles: payload => payload.media_files || [],
        withoutPendingMediaFiles: ({ media_files, ...payload }) => payload,
      }, {
        onJournalEntrySaved: id => { pendingRecord = id; },
        onOpenSavedJournalEntry: id => { openedRecord = id; },
      });
      const id = await q.enqueueDraft('journal', { title: 'photos', media_files: [file('first.png'), file('second.png')] });
      const original = (await q.getAllDrafts())[0];
      await q.syncAllDrafts(f.getOfflineSyncCallbacks());
      const failed = (await q.getAllDrafts())[0];
      check(failed.status === 'failed' && q.getSavedJournalEntryId(failed) === 77 && pendingRecord === 77, 'parent progress missing');
      check(failed.payload._serialized_media.length === 2 && !('media_files' in failed.payload), 'decode mutated attachments');
      check(failed.payload._completed_media_ids.length === 1, 'attachment progress missing');
      await f.refreshOfflineIndicator();
      check(document.body.textContent.includes('offline.discard_attachments'), 'remaining attachment action absent');
      document.querySelector('.offline-saved-btn').click();
      check(openedRecord === 77, 'saved record callback not wired');
      check(await q.retryDraft(id), 'ordinary retry rejected');
      const retried = (await q.getAllDrafts())[0];
      check(retried.operation_id === original.operation_id, 'parent ID changed');
      fail = false;
      await q.syncAllDrafts(f.getOfflineSyncCallbacks());
      check(creates === 1 && uploads.length === 3 && uploads[1][1] === uploads[2][1], 'partial retry duplicated parent or changed media ID');
      check((await q.getAllDrafts()).length === 0, 'successful draft retained');
      passed.push('partial media failure persists parent and stable attachment progress; retry succeeds');

      fail = true;
      await q.enqueueDraft('journal', { media_files: [file('second.png')] });
      await q.syncAllDrafts(f.getOfflineSyncCallbacks());
      await f.refreshOfflineIndicator();
      document.querySelector('.offline-discard-btn').click();
      for (let attempt = 0; attempt < 30 && (await q.getAllDrafts()).length; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 10));
      }
      check((await q.getAllDrafts()).length === 0 && creates === 2, 'discard failed to remove only queued attachments');
      passed.push('open saved record callback and discard remaining attachments');

      for (const status of [404, 409, 410]) {
        check(!q.canRetryFailedDraft({ ...failed, last_status: status }), 'journal conflict renewed');
      }
      passed.push('deleted/conflicting journal requires explicit new submission');

      await reset();
      const taskBodies = [];
      api.taskActionApi = async (_id, body) => { taskBodies.push(body); };
      await q.enqueueDraft('task_complete', { task_id: 'task-1', expected_updated_at_ms: 5, occurred_on: '2026-08-01', observed_plot_ids: [], completed_plant_ids: ['plant-1'] });
      await q.syncAllDrafts(f.getOfflineSyncCallbacks());
      check(taskBodies[0].occurred_on === '2026-08-01' && taskBodies[0].observed_plot_ids.length === 0 && taskBodies[0].completed_plant_ids[0] === 'plant-1', 'task truth fields lost');
      passed.push('task replay preserves occurrence date, explicit empty scope and selected plants');

      await reset();
      // Seed pre-upgrade ownerless rows through real IndexedDB, not enqueueDraft.
      const rawDb = await new Promise((resolve, reject) => {
        const request = indexedDB.open('gardenops-offline');
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      const rawRows = () => new Promise((resolve, reject) => {
        const request = rawDb.transaction('drafts').objectStore('drafts').getAll();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      await new Promise((resolve, reject) => {
        const tx = rawDb.transaction('drafts', 'readwrite');
        for (const [index, status] of ['pending', 'failed'].entries()) {
          tx.objectStore('drafts').add({ type: 'journal', status, garden_id: index + 1,
            operation_id: `legacy-${index}`, created_at_ms: 1, retry_count: 0,
            last_error: 'SECRET_ERROR', payload: { title: 'SECRET_TITLE', _serialized_media: [{ name: 'SECRET_FILE' }] } });
        }
        tx.oncomplete = resolve;
        tx.onabort = tx.onerror = () => reject(tx.error);
      });
      const legacyRows = await rawRows();
      for (const identity of ['alice', 'bob']) {
        q.setOfflineQueueIdentity(identity);
        let replayed = 0;
        const result = await q.syncAllDrafts({ journal: async () => { replayed++; } });
        const snapshot = await q.getOfflineQueueSnapshot(1);
        check(replayed === 0 && result.remaining === 2, 'legacy rows replayed or sync reported empty');
        check(snapshot.quarantinedCount === 2 && snapshot.failedDrafts.length === 0
          && snapshot.pendingCount === 0 && snapshot.taskActions.size === 0, 'legacy snapshot leaked details');
        check((await q.getAllDrafts()).length === 0 && (await q.getPendingDrafts()).length === 0, 'legacy contents listed');
        check(!await q.retryDraft(legacyRows[1].id), 'legacy failure retried');
        await q.removeDraft(legacyRows[0].id);
      }
      await q.clearOfflineQueue();
      q.setOfflineQueueIdentity('bob');
      check(JSON.stringify(await rawRows()) === JSON.stringify(legacyRows), 'legacy rows changed during login cleanup');
      online = true;
      await f.refreshOfflineIndicator();
      const wrapper = document.getElementById('offline-indicator');
      check(!wrapper.hidden && wrapper.textContent.includes('offline.quarantined_count{"count":2}'), 'legacy-only count hidden');
      wrapper.querySelector('.offline-indicator-toggle').click();
      check(!wrapper.querySelector('.offline-failures').hidden && wrapper.textContent.includes('offline.quarantined_notice'), 'generic notice absent');
      check(!wrapper.innerHTML.includes('SECRET') && !wrapper.querySelector('.offline-retry-btn') && !wrapper.querySelector('.offline-saved-btn'), 'legacy details or recovery exposed');
      confirmResult = false;
      wrapper.querySelector('.offline-quarantine-discard-btn').click();
      await new Promise(resolve => setTimeout(resolve, 20));
      check(confirmations.at(-1) === 'offline.discard_quarantined_confirm' && (await rawRows()).length === 2, 'cancelled discard removed legacy rows');
      // Successful owned replay must not announce completion over quarantined work.
      await q.enqueueDraft('task_complete', { task_id: 'mixed', expected_updated_at_ms: 5 });
      toasts.length = 0;
      await f.syncOfflineDraftsNow();
      check(!toasts.includes('offline.sync_complete') && !wrapper.hidden, 'mixed replay falsely announced sync complete');
      online = false;
      await q.enqueueDraft('journal', { title: 'owned survivor' });
      confirmResult = true;
      await f.refreshOfflineIndicator();
      wrapper.querySelector('.offline-quarantine-discard-btn').click();
      for (let attempt = 0; attempt < 30 && (await q.getOfflineQueueSnapshot()).quarantinedCount; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 10));
      }
      check((await q.getOfflineQueueSnapshot()).quarantinedCount === 0, 'confirmed legacy discard failed');
      check((await rawRows()).length === 1 && (await q.getAllDrafts())[0].payload.title === 'owned survivor', 'legacy discard deleted owned work');
      rawDb.close();
      passed.push('ownerless pending/failed rows quarantined across logins; generic count only; confirmed discard preserves owned work; no false sync completion');

      await reset();
      let finishCreate;
      let beganCreate;
      const began = new Promise(resolve => { beganCreate = resolve; });
      api.createJournalEntryApi = () => { beganCreate(); return new Promise(resolve => { finishCreate = resolve; }); };
      const before = uploads.length;
      await q.enqueueDraft('journal', { media_files: [file('late.png')] });
      const sync = q.syncAllDrafts(f.getOfflineSyncCallbacks());
      await began;
      q.setOfflineQueueIdentity('bob');
      finishCreate({ id: 88 });
      await sync;
      check(uploads.length === before, 'upload continued under changed owner');
      q.setOfflineQueueIdentity('alice');
      check(q.getSavedJournalEntryId((await q.getAllDrafts())[0]) === 88, 'confirmed parent lost on identity switch');
      passed.push('in-flight parent result retained but no next request after identity switch');
      await reset();
      return passed;
    }, sources);
    results.forEach(result => console.log(`PASS ${result}`));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

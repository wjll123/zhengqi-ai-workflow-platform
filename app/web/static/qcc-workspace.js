(() => {
  const $ = id => document.getElementById(id);
  let activeId = '', runningId = '', timer = null, validSource = false, anyRunning = false, previewVersion = 0, browserStatuses = [];
  const stateLabels = {running:'查询中', blocked:'等待登录或验证', paused:'已暂停', completed:'本轮完成'};
  const rowLabels = {success:'成功', failed:'失败', pending:'待查询', blocked:'待验证', querying:'查询中'};
  async function api(url, options = {}) {
    const response = await fetch(url, options);
    if (response.status === 401) throw new Error('平台登录已失效，请刷新页面登录。');
    const contentType = response.headers.get('content-type') || '';
    const data = contentType.includes('application/json') ? await response.json()
      : {ok:false, message:response.status === 413
        ? '文件超过服务器上传上限，请压缩文件或联系管理员调整限制。'
        : '服务器返回 HTTP ' + response.status};
    if (!response.ok || data.ok === false) throw new Error(data.message || data.error || '请求失败');
    return data;
  }
  function message(text) { $('qccMsg').textContent = text; }
  function startState() {
    $('qccRun').disabled = !validSource && !anyRunning;
    $('qccRun').textContent = anyRunning ? '查看当前查询进度' : '开始批量查询';
  }
  function fill(id, files, placeholder, preferred = '') {
    const select = $(id), old = preferred || select.value;
    select.replaceChildren(new Option(placeholder, ''));
    for (const name of files) select.add(new Option(name, name));
    if (files.includes(old)) select.value = old;
  }
  async function preview() {
    const source = $('qccSource').value, version = ++previewVersion;
    validSource = false; startState();
    if (!source) { $('qccCount').textContent = '选择名单后显示实际待查数量。'; return; }
    $('qccCount').textContent = '正在核对名单…';
    try {
      const data = await api('/api/qcc/company-count?source_name=' + encodeURIComponent(source));
      if (version !== previewVersion) return;
      validSource = true;
      $('qccCount').textContent = `实际待查 ${data.count} 家（已去重，最多支持 100 家）`;
    } catch (error) { if (version === previewVersion) $('qccCount').textContent = error.message; }
    startState();
  }
  async function refreshFiles(preferred = '') {
    const [inbox, results] = await Promise.all([api('/api/inbox'), api('/api/qcc/previous-results')]);
    const files = inbox.files.filter(name => /\.(xlsx|xls|csv)$/i.test(name));
    fill('qccSource', files, '请选择公司名称名单', preferred);
    fill('qccIdentity', files.filter(name => /\.xlsx$/i.test(name)), '不使用辅助表');
    fill('qccPrevious', results.files, '本次重新查询');
    await preview();
  }
  function draw(job) {
    $('qccProgress').hidden = false;
    const counts = job.counts;
    $('qccState').textContent = stateLabels[job.status] || job.status;
    $('qccState').className = 'tag ' + (job.status === 'blocked' || counts.failed ? 'a' : job.status === 'completed' ? 'g' : '');
    $('qccTotal').textContent = counts.total;
    const complete = counts.full ?? counts.success;
    $('qccSuccess').textContent = complete;
    $('qccFailed').textContent = counts.failed;
    $('qccPending').textContent = counts.total - complete - counts.failed;
    $('qccBar').max = counts.total || 1;
    $('qccBar').value = complete + counts.failed;
    $('qccJobMessage').textContent = job.message;
    $('qccPause').hidden = job.status !== 'running';
    $('qccResume').hidden = job.status === 'running' || complete === counts.total;
    $('qccScope').textContent = `范围：企业详情页披露字段。另有 ${counts.retained || 0} 家保留了旧版基础资料，等待补齐；未采集的独立详情会在 Excel 中注明。`;
    $('qccDownload').href = '/api/qcc/jobs/' + job.id + '/export';
    const fragment = document.createDocumentFragment();
    for (const row of job.rows) {
      const tr = document.createElement('tr');
      const isCurrent = row.name === job.current_name;
      const cells = [row.name, isCurrent ? '查询中' : rowLabels[row.status], row.error || row.note || (row.status === 'success'
        ? `股东 ${row.data.shareholders.rows.length} 条，主要人员 ${row.data.people.rows.length} 条` : '等待查询')];
      for (const value of cells) { const td = document.createElement('td'); td.textContent = value; tr.append(td); }
      fragment.append(tr);
    }
    $('qccRows').replaceChildren(fragment);
  }
  async function refreshJobs(selectId = '') {
    const data = await api('/api/qcc/jobs');
    const runningJob = data.jobs.find(job => job.status === 'running');
    runningId = runningJob?.id || '';
    anyRunning = Boolean(runningJob); startState();
    $('qccActiveNotice').hidden = !runningJob;
    $('qccActiveNotice').textContent = runningJob
      ? `已有批次在后台运行（已补齐 ${runningJob.counts.full ?? runningJob.counts.success}/${runningJob.counts.total} 家）。${runningJob.message}` : '';
    const old = selectId || activeId;
    const selected = data.jobs.find(job => job.id === old) || runningJob || data.jobs[0];
    $('qccHistory').replaceChildren();
    for (const job of data.jobs) $('qccHistory').add(new Option(
      `${job.created_at.replace('T',' ').slice(0,19)} · ${job.source_name} · ${stateLabels[job.status]}`, job.id));
    if (selected) {
      activeId = selected.id; $('qccHistory').value = activeId; draw(selected);
    }
    clearTimeout(timer);
    if (anyRunning) timer = setTimeout(poll, 2000);
  }
  async function poll() {
    try { await refreshJobs(); }
    catch (error) { message('读取进度失败：' + error.message + '；后台结果会继续保存。'); timer = setTimeout(poll, 5000); }
  }
  $('qccSource').addEventListener('change', preview);
  function showBrowser() {
    const browser = $('qccBrowser').value;
    const command = $('qccBrowserCommand');
    if (browser === 'safari') {
      $('qccBrowserHelp').textContent = 'Safari 无需调试端口；请先登录企查查。';
      command.hidden = true;
      $('qccBrowserState').textContent = '使用本机 Safari。';
      return;
    }
    const edge = browser === 'edge', name = edge ? 'Microsoft Edge' : 'Google Chrome';
    const port = edge ? 9223 : 9222;
    $('qccBrowserHelp').textContent = `在 Mac 终端执行下方命令，使用独立调试窗口登录企查查，并保持该标签页打开。日常浏览器可继续使用；若同名调试窗口已打开，请先关闭它再重新执行命令。`;
    command.hidden = false;
    command.textContent = `"/Applications/${name}.app/Contents/MacOS/${name}" --remote-debugging-address=127.0.0.1 --remote-debugging-port=${port} --user-data-dir="$HOME/.zqc-${browser}-debug"`;
    $('qccBrowserState').textContent = browserStatuses.find(item => item.browser === browser)?.message || '请检测浏览器连接。';
  }
  async function refreshBrowserStatus(autoSelect = false) {
    const data = await api('/api/qcc/browser-status');
    browserStatuses = data.browsers;
    if (autoSelect) {
      $('qccBrowser').value = browserStatuses.find(item => item.ready)?.browser || 'chrome';
    }
    showBrowser();
    return browserStatuses;
  }
  $('qccBrowser').addEventListener('change', showBrowser);
  $('qccBrowserCheck').addEventListener('click', async function () {
    this.disabled = true;
    try { await refreshBrowserStatus(); }
    catch (error) { $('qccBrowserState').textContent = '检测失败：' + error.message; }
    this.disabled = false;
  });
  $('qccUpload').addEventListener('change', async function () {
    if (!this.files.length) return;
    const form = new FormData(); form.append('files', this.files[0]);
    message('正在上传…');
    try { const data = await api('/api/upload', {method:'POST', body:form}); await refreshFiles(data.saved[0]); message('上传完成，请核对实际待查数量。'); }
    catch (error) { message(error.message); }
    this.value = '';
  });
  $('qccRun').addEventListener('click', async function () {
    if (anyRunning) {
      if (runningId !== activeId) {
        try { await refreshJobs(runningId); } catch (error) { message(error.message); return; }
      }
      $('qccProgress').scrollIntoView({behavior:'smooth', block:'start'});
      return;
    }
    const max_new_queries = Number($('qccLimit').value), delay_seconds = Number($('qccDelay').value);
    if (!Number.isInteger(max_new_queries) || max_new_queries < 1 || max_new_queries > 100 ||
        !Number.isFinite(delay_seconds) || delay_seconds < 60 || delay_seconds > 600) {
      message('每轮数量需为 1–100 的整数，相邻企业查询间隔需为 60–600 秒；接口调用间隔固定为 3 秒。'); return;
    }
    this.disabled = true; message('正在创建查询任务…');
    try {
      if ($('qccBrowser').value !== 'safari') {
        const statuses = await refreshBrowserStatus();
        const selected = statuses.find(item => item.browser === $('qccBrowser').value);
        if (!selected?.ready) throw new Error(selected?.message || '浏览器调试连接未就绪。');
      }
      const data = await api('/api/qcc/jobs', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
        source_name:$('qccSource').value, resume_from:$('qccPrevious').value,
        identity_source:$('qccIdentity').value, browser:$('qccBrowser').value,
        debug_port:$('qccBrowser').value === 'edge' ? 9223 : 9222,
        max_new_queries, delay_seconds})});
      await refreshJobs(data.job.id); message('任务已启动。可在下方查看进度。');
      $('qccProgress').scrollIntoView({behavior:'smooth', block:'start'});
    } catch (error) { message(error.message); }
    startState();
  });
  for (const [id, action] of [['qccPause','pause'], ['qccResume','resume']]) {
    $(id).addEventListener('click', async function () {
      if (!activeId) return;
      this.disabled = true;
      try {
        const data = await api('/api/qcc/jobs/' + activeId + '/' + action, {method:'POST'});
        message(data.job.message); await refreshJobs(activeId);
      } catch (error) { message(error.message); }
      this.disabled = false;
    });
  }
  $('qccHistory').addEventListener('change', async function () {
    activeId = this.value;
    try { await refreshJobs(activeId); } catch (error) { message(error.message); }
  });
  showBrowser();
  Promise.all([refreshFiles(), refreshJobs(), refreshBrowserStatus(true)]).catch(error => message(error.message));
})();

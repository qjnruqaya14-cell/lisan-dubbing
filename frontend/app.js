// app.js — منطق صفحة الاستوديو: اختيار فيديو، إرسال للسيرفر، تتبع الحالة حيًا.

const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const fileChip = document.getElementById('fileChip');
const fileName = document.getElementById('fileName');
const removeFile = document.getElementById('removeFile');
const langSelect = document.getElementById('langSelect');
const voiceIdInput = document.getElementById('voiceId');
const submitBtn = document.getElementById('submitBtn');
const formError = document.getElementById('formError');
const jobsList = document.getElementById('jobsList');

let selectedFile = null;
const JOBS_STORAGE_KEY = 'lisan_job_ids';
const trackedJobs = new Map(); // job_id -> polling interval id

// ---------- تحميل قائمة اللغات من السيرفر ----------
async function loadLanguages() {
  try {
    const res = await fetch('/api/languages');
    const data = await res.json();
    langSelect.innerHTML = data.languages
      .map(l => `<option value="${l.code}">${l.label}</option>`)
      .join('');
  } catch (e) {
    langSelect.innerHTML = `<option value="">تعذّر تحميل اللغات</option>`;
  }
}

// ---------- اختيار الفيديو ----------
function setFile(file) {
  if (!file) return;
  selectedFile = file;
  fileName.textContent = file.name;
  fileChip.classList.add('show');
  updateSubmitState();
}

dropzone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', e => setFile(e.target.files[0]));

['dragenter', 'dragover'].forEach(evt =>
  dropzone.addEventListener(evt, e => { e.preventDefault(); dropzone.classList.add('drag'); })
);
['dragleave', 'drop'].forEach(evt =>
  dropzone.addEventListener(evt, e => { e.preventDefault(); dropzone.classList.remove('drag'); })
);
dropzone.addEventListener('drop', e => {
  const file = e.dataTransfer.files[0];
  if (file) setFile(file);
});

removeFile.addEventListener('click', e => {
  e.stopPropagation();
  selectedFile = null;
  fileInput.value = '';
  fileChip.classList.remove('show');
  updateSubmitState();
});

function updateSubmitState() {
  submitBtn.disabled = !selectedFile;
}

// ---------- إرسال المهمة ----------
submitBtn.addEventListener('click', async () => {
  if (!selectedFile) return;
  formError.style.display = 'none';
  submitBtn.disabled = true;
  submitBtn.textContent = 'جارٍ الرفع…';

  const formData = new FormData();
  formData.append('video', selectedFile);
  formData.append('target_lang', langSelect.value);
  if (voiceIdInput.value.trim()) {
    formData.append('voice_id', voiceIdInput.value.trim());
  }

  try {
    const res = await fetch('/api/jobs', { method: 'POST', body: formData });
    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || 'تعذّر إنشاء المهمة.');
    }

    saveJobId(data.job_id);
    trackJob(data.job_id);

    // إعادة تصفير النموذج
    selectedFile = null;
    fileInput.value = '';
    fileChip.classList.remove('show');
  } catch (e) {
    formError.textContent = e.message;
    formError.style.display = 'block';
  } finally {
    submitBtn.textContent = 'ابدئي الدبلجة';
    updateSubmitState();
  }
});

// ---------- تخزين معرّفات المهام محليًا (عشان تظهر بعد إعادة فتح الصفحة) ----------
function getSavedJobIds() {
  try {
    return JSON.parse(localStorage.getItem(JOBS_STORAGE_KEY) || '[]');
  } catch (e) {
    return [];
  }
}

function saveJobId(jobId) {
  const ids = getSavedJobIds();
  if (!ids.includes(jobId)) {
    ids.unshift(jobId);
    localStorage.setItem(JOBS_STORAGE_KEY, JSON.stringify(ids.slice(0, 20)));
  }
}

// ---------- عرض وتتبع حالة كل مهمة ----------
function renderEmptyIfNeeded() {
  if (jobsList.children.length === 0) {
    jobsList.innerHTML = `<div class="empty-state">لا يوجد مهام بعد — ارفعي فيديو للبدء.</div>`;
  }
}

function jobCardHtml(job) {
  const statusLabel = {
    queued: 'بالانتظار',
    processing: 'قيد المعالجة',
    done: 'جاهز',
    error: 'خطأ',
  }[job.status] || job.status;

  const progressPct = job.status === 'done' ? 100 : Math.min((job.step || 0) / 6 * 100, 95);

  let actions = '';
  if (job.status === 'done') {
    actions = `
      <div class="job-actions">
        <a class="btn btn-teal" href="${job.output_url}" download>تنزيل</a>
        <a class="btn btn-ghost" href="${job.share_url}" target="_blank">فتح رابط المشاركة</a>
      </div>`;
  }

  return `
    <div class="job-card" data-job-id="${job.id}">
      <div class="top">
        <strong>${job.original_filename || job.id} → ${job.target_lang}</strong>
        <span class="status-pill ${job.status}">${statusLabel}</span>
      </div>
      <div class="progress-bar"><span style="width:${progressPct}%"></span></div>
      <div class="job-msg">${job.message || ''}</div>
      ${actions}
    </div>`;
}

function upsertJobCard(job) {
  const existing = jobsList.querySelector(`[data-job-id="${job.id}"]`);
  const html = jobCardHtml(job);
  if (existing) {
    existing.outerHTML = html;
  } else {
    if (jobsList.querySelector('.empty-state')) jobsList.innerHTML = '';
    jobsList.insertAdjacentHTML('afterbegin', html);
  }
}

async function pollJob(jobId) {
  try {
    const res = await fetch(`/api/jobs/${jobId}`);
    if (!res.ok) return;
    const job = await res.json();
    upsertJobCard(job);

    if (job.status === 'done' || job.status === 'error') {
      clearInterval(trackedJobs.get(jobId));
      trackedJobs.delete(jobId);
    }
  } catch (e) {
    // تجاهل أخطاء الشبكة المؤقتة، المحاولة القادمة تلقائية
  }
}

function trackJob(jobId) {
  if (trackedJobs.has(jobId)) return;
  pollJob(jobId);
  const intervalId = setInterval(() => pollJob(jobId), 3000);
  trackedJobs.set(jobId, intervalId);
}

// ---------- تشغيل ----------
loadLanguages();
getSavedJobIds().forEach(trackJob);
renderEmptyIfNeeded();

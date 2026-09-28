// Turns every <input type="file" data-filepond> into a FilePond drop area.
//
// Files stay in a normal form post (storeAsFile), so the Django views, CSRF protection and
// server-side validation are unchanged; without JavaScript the plain file input still works.
//
// Options come from data-upload-* attributes on the input (not data-accept etc.: FilePond reads
// data-* attributes itself and would take them as its own options):
//   data-upload-accept=".jpg,.png"   allowed extensions (checked in the browser; the server checks again)
//   data-upload-max-size="10MB"      per-file size limit
//   data-upload-image-max="2000"     resize images in the browser to fit this many pixels before upload
//   data-upload-type-hint="…"        text shown when the type is not allowed
(() => {
  if (!window.FilePond || !FilePond.supported()) return;

  FilePond.registerPlugin(
    FilePondPluginFileValidateType,
    FilePondPluginFileValidateSize,
    FilePondPluginImageExifOrientation,
    FilePondPluginImagePreview,
    FilePondPluginImageResize,
    FilePondPluginImageTransform,
  );

  // Browsers disagree on MIME types for spreadsheets and CSV (Windows reports CSV as Excel),
  // so the type is decided by the file extension.
  const MIME_BY_EXT = {
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.xlsm': 'application/vnd.ms-excel.sheet.macroenabled.12',
    '.xls': 'application/vnd.ms-excel',
    '.csv': 'text/csv',
    '.tsv': 'text/tab-separated-values',
    '.txt': 'text/plain',
    '.pdf': 'application/pdf',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
  };
  const extOf = (name) => {
    const m = /\.[^.]+$/.exec(name || '');
    return m ? m[0].toLowerCase() : '';
  };

  const LABELS = {
    labelIdle: '把文件拖到这里、粘贴，或 <span class="filepond--label-action">点击选择</span>',
    labelInvalidField: '有文件无法使用',
    labelFileWaitingForSize: '正在计算大小',
    labelFileSizeNotAvailable: '无法获取大小',
    labelFileLoading: '读取中',
    labelFileLoadError: '读取失败',
    labelFileProcessing: '处理中',
    labelFileProcessingComplete: '已就绪',
    labelFileProcessingAborted: '已取消',
    labelFileProcessingError: '处理失败',
    labelFileRemoveError: '移除失败',
    labelTapToCancel: '点击取消',
    labelTapToRetry: '点击重试',
    labelTapToUndo: '点击撤销',
    labelButtonRemoveItem: '移除',
    labelButtonAbortItemLoad: '取消',
    labelButtonRetryItemLoad: '重试',
    labelButtonAbortItemProcessing: '取消',
    labelButtonUndoItemProcessing: '撤销',
    labelButtonRetryItemProcessing: '重试',
    labelButtonProcessItem: '上传',
    labelMaxFileSizeExceeded: '文件太大',
    labelMaxFileSize: '最大 {filesize}',
    labelMaxTotalFileSizeExceeded: '总大小超出限制',
    labelMaxTotalFileSize: '总大小最大 {filesize}',
    labelFileTypeNotAllowed: '不支持的文件类型',
  };

  document.querySelectorAll('input[type="file"][data-filepond]').forEach((input) => {
    const form = input.closest('form');
    const submit = form && form.querySelector('button[type="submit"]:not([name])');
    const submitText = submit ? submit.textContent : '';
    const accept = (input.dataset.uploadAccept || '').split(',').map((s) => s.trim()).filter(Boolean);
    const imageMax = parseInt(input.dataset.uploadImageMax || '0', 10);
    const prepared = new Set(); // ids of images already resized in the browser
    let pond = null;

    // Recomputed from the pond's current files on every change, so it does not depend on the
    // order in which FilePond fires its add / prepare events.
    const busy = () => (pond && imageMax > 0 ? pond.getFiles().filter((f) =>
      /^image\//.test(f.fileType) && f.status !== FilePond.FileStatus.LOAD_ERROR
      && !prepared.has(f.id)).length : 0);
    const setBusy = () => {
      if (!submit) return;
      const n = busy();
      submit.disabled = n > 0;
      submit.textContent = n > 0 ? '正在处理图片…' : submitText;
    };

    // FilePond also reads the input's native `accept` attribute (kept for the no-JS file picker),
    // which may list extensions; its type check needs MIME types, so set these after creation.
    const typeOptions = {
      acceptedFileTypes: accept.map((ext) => MIME_BY_EXT[ext]).filter(Boolean),
      maxFileSize: input.dataset.uploadMaxSize || null,
    };
    pond = FilePond.create(input, {
      ...LABELS,
      storeAsFile: true,
      credits: false,
      allowMultiple: input.multiple,
      allowReorder: input.multiple,
      name: input.name,
      required: input.required,
      ...typeOptions,
      fileValidateTypeDetectType: (file, type) =>
        Promise.resolve(MIME_BY_EXT[extOf(file.name)] || type),
      fileValidateTypeLabelExpectedTypes: input.dataset.uploadTypeHint || `支持 ${accept.join(' ')}`,
      imagePreviewHeight: 170,
      allowImageResize: imageMax > 0,
      allowImageTransform: imageMax > 0,
      imageResizeTargetWidth: imageMax || null,
      imageResizeTargetHeight: imageMax || null,
      imageResizeMode: 'contain',
      imageResizeUpscale: false,
      imageTransformOutputQuality: 85,
      // Only resize images; spreadsheets and PDFs are posted untouched.
      beforePrepareFile: (item, shouldPrepare) => shouldPrepare && /^image\//.test(item.fileType),
      onaddfile: () => setBusy(),
      onpreparefile: (item) => { prepared.add(item.id); setBusy(); },
      onremovefile: (error, item) => { prepared.delete(item.id); setBusy(); },
      onupdatefiles: () => setBusy(),
    });

    pond.setOptions(typeOptions);

    // Never post a form while a file the browser rejected is still in the list.
    if (form) {
      form.addEventListener('submit', (event) => {
        const invalid = pond.getFiles().some((f) => f.status === FilePond.FileStatus.LOAD_ERROR);
        if (invalid || busy() > 0) {
          event.preventDefault();
          alert(invalid ? '请先移除标红的文件' : '图片还在处理中，请稍候');
        }
      });
    }
  });
})();

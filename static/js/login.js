// Login page behaviour: show/hide password, Caps Lock hint, required-field check, busy state.
(() => {
  const form = document.querySelector('#login-form');
  const username = document.querySelector('#id_username');
  const password = document.querySelector('#id_password');
  const toggle = document.querySelector('#toggle-pw');
  const capsHint = document.querySelector('#caps-hint');
  const submitBtn = document.querySelector('#submit');
  const submitLabel = submitBtn.querySelector('.submit-label');
  const formError = document.querySelector('#form-error');

  document.querySelector('#year').textContent = new Date().getFullYear();

  // 服务端有错误时（Django 渲染出内容），直接显示
  if (formError.textContent.trim()) formError.classList.add('is-visible');

  const setFieldError = (input, message) => {
    const hint = document.querySelector(`#${input.name}-hint`);
    if (message) {
      input.setAttribute('aria-invalid', 'true');
      hint.textContent = message;
      hint.hidden = false;
    } else {
      input.removeAttribute('aria-invalid');
      hint.textContent = '';
      hint.hidden = true;
    }
  };

  // 显示 / 隐藏密码
  toggle.addEventListener('click', () => {
    const showing = password.type === 'text';
    password.type = showing ? 'password' : 'text';
    toggle.textContent = showing ? '显示' : '隐藏';
    toggle.setAttribute('aria-pressed', String(!showing));
    password.focus();
  });

  // 大写锁定提示
  const checkCaps = (event) => {
    if (typeof event.getModifierState !== 'function') return;
    capsHint.hidden = !event.getModifierState('CapsLock');
  };
  password.addEventListener('keydown', checkCaps);
  password.addEventListener('keyup', checkCaps);
  password.addEventListener('blur', () => { capsHint.hidden = true; });

  // 输入时清掉该字段的错误
  [username, password].forEach((input) => {
    input.addEventListener('input', () => {
      setFieldError(input, '');
      formError.classList.remove('is-visible');
    });
  });

  // 提交前只做必填校验，账号密码是否正确交给 Django
  form.addEventListener('submit', (event) => {
    const checks = [
      [username, '请输入用户名'],
      [password, '请输入密码'],
    ];
    let firstInvalid = null;

    checks.forEach(([input, message]) => {
      const empty = !input.value.trim();
      setFieldError(input, empty ? message : '');
      if (empty && !firstInvalid) firstInvalid = input;
    });

    if (firstInvalid) {
      event.preventDefault();
      firstInvalid.focus();
      return;
    }

    submitBtn.disabled = true;
    submitBtn.classList.add('is-loading');
    submitLabel.textContent = '正在登录';
  });

  // 浏览器后退回到页面时恢复按钮
  window.addEventListener('pageshow', () => {
    submitBtn.disabled = false;
    submitBtn.classList.remove('is-loading');
    submitLabel.textContent = '登录';
  });
})();

(function () {
  if (window.__sqat_installed__) return;
  window.__sqat_installed__ = true;

  const STORE_PASSWORD_VALUES = window.__sqat_store_password_values__ === true;
  const focusState = new WeakMap();
  const dirtyInputs = new WeakSet();
  const lastInputCommit = new WeakMap();
  const BARE = new Set(['a', 'button', 'input', 'select', 'textarea', 'div', 'span']);

  function text(v, n = 200) {
    return (v || '').toString().replace(/\s+/g, ' ').trim().substring(0, n);
  }
  function esc(v) {
    return String(v || '').replace(/["\\]/g, '\\$&');
  }
  function css(v) {
    return window.CSS && CSS.escape ? CSS.escape(v) : esc(v);
  }
  function attr(el, name) {
    return el && el.getAttribute ? el.getAttribute(name) : null;
  }
  function visible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    const s = window.getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  }
  function cleanHref(el) {
    const href = attr(el, 'href');
    return href ? href.trim() : '';
  }
  function meaningfulHref(el) {
    const href = cleanHref(el).toLowerCase();
    return !!href && href !== '#' && href !== 'javascript:void(0)' && href !== 'javascript:;';
  }
  function adLike(el) {
    if (!el || !el.tagName) return false;
    const bits = [
      attr(el, 'aria-label'),
      attr(el, 'title'),
      attr(el, 'id'),
      attr(el, 'name'),
      attr(el, 'src'),
      attr(el, 'class'),
    ].join(' ').toLowerCase();
    return /advertisement|googleads|doubleclick|pagead|adsystem|adservice|googlesyndication/.test(bits);
  }
  function stableId(id) {
    return !!id && !/^[0-9a-f-]{16,}$/i.test(id) && !/^(ember|react|radix|headlessui|mui|chakra|mantine|rc|auto|generated)[-_]?[0-9]/i.test(id);
  }
  function role(el) {
    const explicit = attr(el, 'role');
    if (explicit) return explicit;
    const tag = (el.tagName || '').toLowerCase();
    const type = (el.type || '').toLowerCase();
    if (tag === 'button' || type === 'button' || type === 'submit') return 'button';
    if (tag === 'a' && attr(el, 'href')) return 'link';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'range') return 'slider';
      return 'textbox';
    }
    return tag || null;
  }
  function label(el) {
    const labelledBy = attr(el, 'aria-labelledby');
    if (labelledBy) {
      const t = labelledBy.split(/\s+/).map(id => text(document.getElementById(id)?.textContent)).filter(Boolean).join(' ');
      if (t) return t;
    }
    if (el.id) {
      const l = document.querySelector(`label[for="${esc(el.id)}"]`);
      if (l) return text(l.textContent);
    }
    const wrap = el.closest ? el.closest('label') : null;
    return wrap ? text(wrap.textContent) : '';
  }
  function nameOf(el) {
    return text(attr(el, 'aria-label')) || label(el) || text(attr(el, 'alt')) || text(attr(el, 'title')) || text(el.placeholder) || text(el.innerText || el.textContent || el.value, 120);
  }
  function locator(el, accessible, r) {
    const safe = String(accessible || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/'/g, "\\'");
    if (r && safe) return `page.getByRole('${r}', { name: /${safe}/i })`;
    const l = label(el).replace(/'/g, "\\'");
    if (l && ['input', 'textarea', 'select'].includes((el.tagName || '').toLowerCase())) return `page.getByLabel('${l}')`;
    return null;
  }
  function selector(el) {
    if (!el || !el.tagName) return null;
    const tag = el.tagName.toLowerCase();
    for (const a of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
      const v = attr(el, a);
      if (v) return { selector: `[${a}="${esc(v)}"]`, stability: 'high', quality_reason: 'data_attr' };
    }
    const aria = attr(el, 'aria-label');
    if (aria) return { selector: `${tag}[aria-label="${esc(aria)}"]`, stability: 'high', quality_reason: 'role_name' };
    if (el.id && stableId(el.id)) return { selector: `#${css(el.id)}`, stability: 'high', quality_reason: 'stable_id' };
    if (el.name) return { selector: `${tag}[name="${esc(el.name)}"]`, stability: 'medium', quality_reason: 'placeholder' };
    if (el.placeholder) return { selector: `${tag}[placeholder="${esc(el.placeholder)}"]`, stability: 'medium', quality_reason: 'placeholder' };
    if (tag === 'a' && meaningfulHref(el)) return { selector: `${tag}[href="${esc(cleanHref(el))}"]`, stability: 'medium', quality_reason: 'href' };
    const r = role(el);
    const n = nameOf(el);
    if (r && n && !['div', 'span', 'i', 'svg', 'path'].includes(tag)) return { selector: domPath(el) || tag, stability: 'low', quality_reason: 'role_name_fallback' };
    return { selector: domPath(el) || tag, stability: 'low', quality_reason: 'structural_fallback' };
  }
  function selectorCandidates(el) {
    if (!el || !el.tagName) return [];
    const tag = el.tagName.toLowerCase();
    const out = [];
    const push = v => {
      if (v && !out.includes(v)) out.push(v);
    };
    for (const a of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
      const v = attr(el, a);
      if (v) push(`[${a}="${esc(v)}"]`);
    }
    if (attr(el, 'aria-label')) push(`${tag}[aria-label="${esc(attr(el, 'aria-label'))}"]`);
    if (el.id && stableId(el.id)) push(`#${css(el.id)}`);
    if (el.name) push(`${tag}[name="${esc(el.name)}"]`);
    if (el.placeholder) push(`${tag}[placeholder="${esc(el.placeholder)}"]`);
    if (tag === 'a' && meaningfulHref(el)) push(`${tag}[href="${esc(cleanHref(el))}"]`);
    const l = label(el);
    if (l && ['input', 'textarea', 'select'].includes(tag)) push(`label:${l}`);
    const r = role(el);
    const n = nameOf(el);
    if (r && n) push(`role:${r}[name="${n}"]`);
    push(domPath(el));
    return out.slice(0, 8);
  }
  function domPath(el) {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 8) {
      const tag = node.tagName.toLowerCase();
      if (node.id && stableId(node.id)) {
        parts.unshift(`${tag}#${css(node.id)}`);
        break;
      }
      let i = 1;
      let p = node.previousElementSibling;
      while (p) {
        if (p.tagName === node.tagName) i += 1;
        p = p.previousElementSibling;
      }
      parts.unshift(`${tag}:nth-of-type(${i})`);
      node = node.parentElement;
    }
    return parts.join(' > ');
  }
  function state(el) {
    if (!el) return null;
    const type = (el.type || '').toLowerCase();
    const redacted = type === 'password' && !STORE_PASSWORD_VALUES;
    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
    const opt = el.tagName?.toLowerCase() === 'select' ? el.options?.[el.selectedIndex] : null;
    return {
      value: redacted ? null : (el.value == null ? null : String(el.value).substring(0, 500)),
      value_redacted: redacted,
      checked: typeof el.checked === 'boolean' ? el.checked : null,
      selected_value: el.tagName?.toLowerCase() === 'select' ? el.value : null,
      selected_label: opt ? text(opt.label || opt.textContent) : null,
      aria_expanded: attr(el, 'aria-expanded'),
      aria_selected: attr(el, 'aria-selected'),
      aria_checked: attr(el, 'aria-checked'),
      aria_pressed: attr(el, 'aria-pressed'),
      aria_invalid: attr(el, 'aria-invalid'),
      required: el.required === true || attr(el, 'aria-required') === 'true',
      disabled: el.disabled === true || attr(el, 'aria-disabled') === 'true',
      min: attr(el, 'min'),
      max: attr(el, 'max'),
      step: attr(el, 'step'),
      rect: r ? {
        x: Math.round(r.x),
        y: Math.round(r.y),
        width: Math.round(r.width),
        height: Math.round(r.height),
        page_x: Math.round(r.x + window.scrollX),
        page_y: Math.round(r.y + window.scrollY),
      } : null,
    };
  }
  function options(el) {
    if (el && el.tagName?.toLowerCase() === 'select') {
      return Array.from(el.options || []).map((o, i) => ({
        index: i,
        value: o.value,
        label: text(o.label || o.textContent),
        selected: o.selected,
        disabled: o.disabled,
      }));
    }
    const controlled = el ? document.getElementById(attr(el, 'aria-controls')) : null;
    const root = controlled || document;
    return Array.from(root.querySelectorAll('[role="listbox"] [role="option"],[role="option"],[role="menu"] [role="menuitem"],[role="menuitem"]'))
      .filter(visible)
      .slice(0, 80)
      .map((o, i) => ({
        index: i,
        text: nameOf(o) || text(o.textContent),
        value: attr(o, 'data-value') || attr(o, 'value'),
        role: role(o),
        selected: attr(o, 'aria-selected') === 'true' || attr(o, 'aria-checked') === 'true',
        disabled: o.disabled === true || attr(o, 'aria-disabled') === 'true',
        selector: selector(o)?.selector || null,
      }));
  }
  function group(el) {
    const type = (el.type || '').toLowerCase();
    if (el.name && ['checkbox', 'radio'].includes(type)) {
      return {
        name: el.name,
        role: type === 'radio' ? 'radiogroup' : 'checkbox-group',
        options: Array.from(document.querySelectorAll(`input[type="${type}"][name="${esc(el.name)}"]`)).map((n, i) => ({
          index: i,
          label: nameOf(n),
          value: n.value || null,
          checked: n.checked === true,
          selector: selector(n)?.selector || null,
        })),
      };
    }
    const g = el.closest?.('[role="radiogroup"],[role="group"],fieldset');
    if (!g) return null;
    return {
      label: nameOf(g) || text(g.querySelector('legend')?.textContent),
      role: attr(g, 'role') || g.tagName.toLowerCase(),
      options: Array.from(g.querySelectorAll('[role="radio"],[role="checkbox"],[role="switch"],input[type="radio"],input[type="checkbox"]')).map((n, i) => ({
        index: i,
        label: nameOf(n),
        checked: n.checked === true || attr(n, 'aria-checked') === 'true',
        selector: selector(n)?.selector || null,
      })),
    };
  }
  function parent(el) {
    const p = el.closest?.('form,nav,header,main,section,article,[role="dialog"],[role="navigation"],[role="main"],[role="group"],fieldset') || el.parentElement;
    return p ? {
      tag: p.tagName.toLowerCase(),
      role: role(p),
      label: nameOf(p) || text(p.querySelector?.('legend,h1,h2,h3')?.textContent),
      text: text(p.textContent, 300),
      selector: selector(p)?.selector || null,
    } : null;
  }
  function actionableTarget(el) {
    if (!ok(el)) return null;
    const target = el.closest?.([
      'button',
      'a[href]',
      'input[type="button"]',
      'input[type="submit"]',
      'input[type="reset"]',
      'label',
      '[role="button"]',
      '[role="link"]',
      '[role="menuitem"]',
      '[role="menuitemcheckbox"]',
      '[role="menuitemradio"]',
      '[role="tab"]',
      '[role="option"]',
      '[role="checkbox"]',
      '[role="radio"]',
      '[role="switch"]',
      '[tabindex]:not([tabindex="-1"])',
    ].join(','));
    if (target && ok(target) && visible(target) && !adLike(target)) return target;
    return adLike(el) ? null : el;
  }
  function snapshot(el) {
    const s = selector(el);
    const r = role(el);
    const n = nameOf(el);
    const loc = locator(el, n, r);
    return {
      selector: s?.selector || null,
      selector_candidates: selectorCandidates(el),
      selector_stability: (loc && (loc.includes('getByRole') || loc.includes('getByLabel'))) ? 'high' : (s?.stability || 'low'),
      playwright_locator: loc,
      accessible_name: n || null,
      visible_text: text(el.innerText || el.textContent, 160),
      label: label(el) || null,
      role: r,
      tag: el.tagName?.toLowerCase() || null,
      type: el.type || null,
      id: el.id || null,
      name: el.name || null,
      class: attr(el, 'class'),
      href: attr(el, 'href'),
      dom_path: domPath(el),
      parent_context: parent(el),
      state: state(el),
      options: options(el),
      group: group(el),
      attributes: {
        'data-testid': attr(el, 'data-testid'),
        'data-test': attr(el, 'data-test'),
        'data-cy': attr(el, 'data-cy'),
        'data-qa': attr(el, 'data-qa'),
        'aria-label': attr(el, 'aria-label'),
        'aria-controls': attr(el, 'aria-controls'),
      },
    };
  }
  function ok(el) {
    return el && el.tagName && !['HTML', 'BODY'].includes(el.tagName) && !(el.closest && el.closest('#__sqat_capture_indicator__')) && !adLike(el);
  }
  function action(type, el, extra = {}) {
    const snap = snapshot(el);
    return {
      type,
      selector: snap.selector,
      selectorCandidates: snap.selector_candidates,
      stability: snap.selector_stability,
      playwrightLocator: snap.playwright_locator,
      text: snap.visible_text,
      accessibleName: snap.accessible_name,
      role: snap.role,
      label: snap.label,
      inputType: snap.type,
      elementType: snap.tag,
      value: extra.value,
      inputValueKind: extra.inputValueKind,
      url: window.location.href,
      urlBefore: window.location.href,
      beforeState: extra.beforeState || snap.state,
      afterState: extra.afterState || null,
      semanticContext: {
        element: snap,
        parent_context: snap.parent_context,
        options: snap.options || [],
        group: snap.group || null,
        event: extra.event || null,
        low_selector_reason: BARE.has(String(snap.selector || '').toLowerCase()) ? 'semantic CSS selector unavailable; use playwright_locator or parent_context' : null,
      },
    };
  }
  function inputKind(el) {
    const tag = (el.tagName || '').toLowerCase();
    const t = (el.type || '').toLowerCase();
    if (tag === 'select') return 'select';
    if (t === 'range') return 'slide';
    if (['checkbox', 'radio'].includes(t)) return el.checked ? 'check' : 'uncheck';
    return 'fill';
  }
  function recordableInput(el) {
    if (!ok(el)) return false;
    const tag = (el.tagName || '').toLowerCase();
    const t = (el.type || '').toLowerCase();
    if (!['input', 'textarea', 'select'].includes(tag)) return false;
    return !['button', 'submit', 'reset', 'image', 'hidden', 'file'].includes(t);
  }
  function inputValue(el) {
    const t = (el.type || '').toLowerCase();
    const redacted = t === 'password' && !STORE_PASSWORD_VALUES;
    return redacted ? null : (el.value || '').substring(0, 500);
  }
  function inputValueKind(el) {
    const t = (el.type || '').toLowerCase();
    const hints = `${t} ${el.name || ''} ${el.id || ''} ${attr(el, 'autocomplete') || ''} ${label(el) || ''} ${el.placeholder || ''}`.toLowerCase();
    if (t === 'password' || /password|username|email|login|credential/.test(hints)) return 'credential';
    if ((el.value || '') === '') return 'empty';
    if ((el.tagName || '').toLowerCase() === 'select') return 'option_value';
    return 'literal';
  }
  function inputCommitKey(el) {
    const t = (el.type || '').toLowerCase();
    const rawValue = t === 'password'
      ? String(el.value || '').substring(0, 500)
      : String(inputValue(el) ?? '');
    return `${inputKind(el)}|${rawValue}|${el.checked === true}`;
  }
  function commitInput(el, event = {}) {
    if (!recordableInput(el)) return;
    const force = event.force === true || event.source === 'change';
    if (!force && !dirtyInputs.has(el)) return;
    const key = inputCommitKey(el);
    if (lastInputCommit.get(el) === key) return;
    lastInputCommit.set(el, key);
    window.__sqat_action__(action(inputKind(el), el, {
      value: inputValue(el),
      inputValueKind: inputValueKind(el),
      beforeState: focusState.get(el) || null,
      afterState: state(el),
      event,
    })).catch(() => {});
  }
  function flushActiveInput(source) {
    const active = document.activeElement;
    if (active) commitInput(active, { committed: true, source });
  }

  document.addEventListener('focusin', e => {
    if (ok(e.target)) focusState.set(e.target, state(e.target));
  }, true);
  document.addEventListener('pointerdown', e => {
    if (ok(e.target) && e.target !== document.activeElement) flushActiveInput('pointerdown');
    if (ok(e.target)) focusState.set(e.target, state(e.target));
  }, true);
  document.addEventListener('focusout', e => {
    commitInput(e.target, { committed: true, source: 'blur' });
  }, true);
  document.addEventListener('click', e => {
    if (!ok(e.target)) return;
    flushActiveInput('click');
    const el = actionableTarget(e.target);
    if (!el) return;
    const tag = el.tagName.toLowerCase();
    const nativeType = (el.type || '').toLowerCase();
    if (['input', 'textarea', 'select'].includes(tag) && !['button', 'submit', 'reset'].includes(nativeType)) return;
    let type = 'click';
    const r = role(el);
    if (r === 'button' && ((el.type || '').toLowerCase() === 'submit' || text(el.innerText || el.value).toLowerCase() === 'submit')) type = 'submit';
    window.__sqat_action__(action(type, el, {
      beforeState: focusState.get(el) || state(el),
      event: { x: Math.round(e.clientX), y: Math.round(e.clientY), button: e.button },
    })).catch(() => {});
  }, true);
  document.addEventListener('change', e => {
    if (!ok(e.target)) return;
    const el = e.target;
    const tag = el.tagName.toLowerCase();
    if (!['input', 'textarea', 'select'].includes(tag)) return;
    commitInput(el, { committed: true, source: 'change', force: true });
  }, true);
  document.addEventListener('input', e => {
    if (!ok(e.target)) return;
    const el = e.target;
    const tag = el.tagName.toLowerCase();
    const t = (el.type || '').toLowerCase();
    if (!['input', 'textarea'].includes(tag) || ['checkbox', 'radio', 'range', 'button', 'submit', 'reset'].includes(t)) return;
    dirtyInputs.add(el);
  }, true);
  document.addEventListener('keydown', e => {
    if (!['Enter', 'Tab'].includes(e.key) || !ok(e.target)) return;
    commitInput(e.target, { committed: true, source: 'keydown', key: e.key });
    window.__sqat_action__(action('keypress', e.target, {
      value: e.key,
      beforeState: focusState.get(e.target) || state(e.target),
      event: { key: e.key },
    })).catch(() => {});
  }, true);
})();

window.__sqat_enrich_after_action__ = function (action) {
  const text = (v, n = 200) => (v || '').toString().replace(/\s+/g, ' ').trim().substring(0, n);
  const attr = (el, name) => el && el.getAttribute ? el.getAttribute(name) : null;
  let el = null;
  try {
    const sel = String(action?.selector || '');
    if (sel && !['a', 'button', 'input', 'select', 'textarea', 'div', 'span'].includes(sel.toLowerCase())) el = document.querySelector(sel);
  } catch (_) {}
  const visible = node => {
    if (!node || !node.getBoundingClientRect) return false;
    const r = node.getBoundingClientRect();
    const s = getComputedStyle(node);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const optionRoot = el && attr(el, 'aria-controls') ? document.getElementById(attr(el, 'aria-controls')) : document;
  const visibleOptions = el && el.tagName?.toLowerCase() === 'select'
    ? Array.from(el.options || []).map((o, i) => ({ index: i, value: o.value, label: text(o.label || o.textContent), selected: o.selected, disabled: o.disabled }))
    : Array.from(optionRoot.querySelectorAll('[role="listbox"] [role="option"],[role="option"],[role="menu"] [role="menuitem"],[role="menuitem"]')).filter(visible).slice(0, 80).map((o, i) => ({
        index: i,
        text: text(attr(o, 'aria-label') || o.textContent),
        value: attr(o, 'data-value') || attr(o, 'value'),
        role: attr(o, 'role') || o.tagName.toLowerCase(),
        selected: attr(o, 'aria-selected') === 'true' || attr(o, 'aria-checked') === 'true',
        disabled: o.disabled === true || attr(o, 'aria-disabled') === 'true',
      }));
  return {
    url: window.location.href,
    title: document.title,
    after_state: el ? {
      value: el.type === 'password' && !window.__sqat_store_password_values__ ? null : (el.value ?? null),
      value_redacted: el.type === 'password' && !window.__sqat_store_password_values__,
      checked: typeof el.checked === 'boolean' ? el.checked : null,
      selected_value: el.tagName?.toLowerCase() === 'select' ? el.value : null,
      selected_label: el.tagName?.toLowerCase() === 'select' ? text(el.options?.[el.selectedIndex]?.label || el.options?.[el.selectedIndex]?.textContent) : null,
      aria_expanded: attr(el, 'aria-expanded'),
      aria_selected: attr(el, 'aria-selected'),
      aria_checked: attr(el, 'aria-checked'),
      aria_pressed: attr(el, 'aria-pressed'),
    } : null,
    visible_options: visibleOptions,
  };
};

window.__sqat_show_capture_indicator__ = function () {
  let el = document.getElementById('__sqat_capture_indicator__');
  if (!el) {
    el = document.createElement('div');
    el.id = '__sqat_capture_indicator__';
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = '';
    const style = document.createElement('style');
    style.id = '__sqat_capture_indicator_style__';
    style.textContent = '#__sqat_capture_indicator__{position:fixed;inset:0;pointer-events:none;z-index:2147483647;box-shadow:inset 0 0 0 3px rgba(125,211,252,.95),inset 0 0 18px rgba(125,211,252,.45);opacity:0;transition:opacity 80ms ease}#__sqat_capture_indicator__.on{opacity:1}';
    document.documentElement.appendChild(style);
    document.documentElement.appendChild(el);
  }
  requestAnimationFrame(() => el.classList.add('on'));
};
window.__sqat_hide_capture_indicator__ = function () {
  const el = document.getElementById('__sqat_capture_indicator__');
  if (el) el.classList.remove('on');
};

window.__sqat_get_elements__ = function () {
  const out = [];
  const seen = new Set();
  const selectors = [
    'button:not([disabled])',
    'a[href]',
    'input:not([type="hidden"])',
    'select',
    'textarea',
    '[role="button"]',
    '[role="link"]',
    '[role="tab"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="switch"]',
    '[role="combobox"]',
    '[role="option"]',
    '[role="menuitem"]',
    '[tabindex]:not([tabindex="-1"])',
  ];
  const text = (v, n = 160) => (v || '').toString().replace(/\s+/g, ' ').trim().substring(0, n);
  const attr = (el, name) => el && el.getAttribute ? el.getAttribute(name) : null;
  const esc = v => String(v || '').replace(/["\\]/g, '\\$&');
  const stableId = id => !!id && !/^[0-9a-f-]{16,}$/i.test(id);
  const role = el => attr(el, 'role') || (el.tagName || '').toLowerCase();
  const name = el => text(attr(el, 'aria-label')) || text(el.innerText || el.textContent || el.value);
  const meaningfulHref = el => {
    const href = String(attr(el, 'href') || '').trim().toLowerCase();
    return !!href && href !== '#' && href !== 'javascript:void(0)' && href !== 'javascript:;';
  };
  const adLike = el => /advertisement|googleads|doubleclick|pagead|adsystem|adservice|googlesyndication/.test([
    attr(el, 'aria-label'),
    attr(el, 'title'),
    attr(el, 'id'),
    attr(el, 'name'),
    attr(el, 'src'),
    attr(el, 'class'),
  ].join(' ').toLowerCase());
  const path = el => {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 8) {
      const tag = node.tagName.toLowerCase();
      if (node.id && stableId(node.id)) {
        parts.unshift(`${tag}#${window.CSS && CSS.escape ? CSS.escape(node.id) : esc(node.id)}`);
        break;
      }
      let i = 1;
      let p = node.previousElementSibling;
      while (p) {
        if (p.tagName === node.tagName) i += 1;
        p = p.previousElementSibling;
      }
      parts.unshift(`${tag}:nth-of-type(${i})`);
      node = node.parentElement;
    }
    return parts.join(' > ');
  };
  const candidates = el => {
    const tag = el.tagName.toLowerCase();
    const out = [];
    const push = v => {
      if (v && !out.includes(v)) out.push(v);
    };
    for (const a of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
      const v = attr(el, a);
      if (v) push(`[${a}="${esc(v)}"]`);
    }
    if (attr(el, 'aria-label')) push(`${tag}[aria-label="${esc(attr(el, 'aria-label'))}"]`);
    if (el.id && stableId(el.id)) push(`#${window.CSS && CSS.escape ? CSS.escape(el.id) : esc(el.id)}`);
    if (el.name) push(`${tag}[name="${esc(el.name)}"]`);
    if (el.placeholder) push(`${tag}[placeholder="${esc(el.placeholder)}"]`);
    if (tag === 'a' && meaningfulHref(el)) push(`${tag}[href="${esc(attr(el, 'href'))}"]`);
    const labelled = el.id ? text(document.querySelector(`label[for="${esc(el.id)}"]`)?.textContent) : '';
    if (labelled && ['input', 'textarea', 'select'].includes(tag)) push(`label:${labelled}`);
    const r = role(el);
    const n = name(el);
    if (r && n) push(`role:${r}[name="${n}"]`);
    push(path(el));
    return out.slice(0, 8);
  };
  const best = el => {
    const tag = el.tagName.toLowerCase();
    for (const a of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
      const v = attr(el, a);
      if (v) return { selector: `[${a}="${esc(v)}"]`, selector_stability: 'high' };
    }
    if (attr(el, 'aria-label')) return { selector: `${tag}[aria-label="${esc(attr(el, 'aria-label'))}"]`, selector_stability: 'high' };
    if (el.id && stableId(el.id)) return { selector: `#${window.CSS && CSS.escape ? CSS.escape(el.id) : esc(el.id)}`, selector_stability: 'high' };
    if (el.name) return { selector: `${tag}[name="${esc(el.name)}"]`, selector_stability: 'medium' };
    if (el.placeholder) return { selector: `${tag}[placeholder="${esc(el.placeholder)}"]`, selector_stability: 'medium' };
    if (tag === 'a' && meaningfulHref(el)) return { selector: `${tag}[href="${esc(attr(el, 'href'))}"]`, selector_stability: 'medium' };
    return { selector: candidates(el).at(-1) || tag, selector_stability: 'low' };
  };
  const optionList = el => {
    if (el.tagName?.toLowerCase() !== 'select') return [];
    return Array.from(el.options || []).map((o, i) => ({ index: i, value: o.value, label: text(o.label || o.textContent), selected: o.selected, disabled: o.disabled }));
  };

  selectors.forEach(sel => {
    document.querySelectorAll(sel).forEach(el => {
      if (seen.has(el)) return;
      seen.add(el);
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      if (rect.width === 0 || rect.height === 0 || style.display === 'none' || style.visibility === 'hidden' || adLike(el)) return;
      const b = best(el);
      out.push({
        tag: el.tagName.toLowerCase(),
        type: el.type || null,
        role: role(el),
        text: text(el.innerText || el.textContent),
        accessible_name: name(el),
        selector_candidates: candidates(el),
        label: text(document.querySelector(`label[for="${esc(el.id)}"]`)?.textContent),
        placeholder: el.placeholder || null,
        name: el.name || null,
        id: el.id || null,
        class: attr(el, 'class'),
        href: el.tagName.toLowerCase() === 'a' ? attr(el, 'href') : null,
        disabled: el.disabled === true || attr(el, 'aria-disabled') === 'true',
        required: el.required === true || attr(el, 'aria-required') === 'true',
        checked: typeof el.checked === 'boolean' ? el.checked : null,
        value_redacted: (el.type || '').toLowerCase() === 'password',
        selected_value: el.tagName.toLowerCase() === 'select' ? el.value : null,
        selected_label: el.tagName.toLowerCase() === 'select' ? text(el.options?.[el.selectedIndex]?.label || el.options?.[el.selectedIndex]?.textContent) : null,
        aria_expanded: attr(el, 'aria-expanded'),
        aria_selected: attr(el, 'aria-selected'),
        aria_checked: attr(el, 'aria-checked'),
        options: optionList(el),
        ...b,
        rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
      });
    });
  });
  return out;
};

window.__sqat_clean_html__ = function () {
  const clone = document.documentElement.cloneNode(true);
  clone.querySelectorAll('style,script,link[rel="stylesheet"],link[rel="preload"],noscript,meta,template,[aria-hidden="true"],#__sqat_capture_indicator__,#__sqat_capture_indicator_style__').forEach(el => el.remove());
  clone.querySelectorAll('iframe[aria-label*="Advertisement" i],iframe[title*="Advertisement" i],iframe[src*="googleads"],iframe[src*="doubleclick"],iframe[src*="pagead"],iframe[src*="googlesyndication"],ins[id*="aswift"],div[id*="aswift"],iframe[name*="googlefc"]').forEach(el => el.remove());
  clone.querySelectorAll('svg *').forEach(el => {
    ['d', 'points', 'transform', 'viewBox', 'fill', 'stroke', 'clip-path', 'filter', 'mask', 'opacity'].forEach(a => el.removeAttribute(a));
  });
  const keep = new Set([
    'id', 'name', 'type', 'href', 'src', 'alt', 'placeholder', 'role', 'for', 'action', 'method',
    'value', 'checked', 'selected', 'disabled', 'required', 'readonly', 'multiple', 'tabindex', 'target',
    'aria-label', 'aria-labelledby', 'aria-describedby', 'aria-expanded', 'aria-selected',
    'aria-checked', 'aria-disabled', 'aria-controls', 'aria-current', 'aria-invalid', 'aria-required',
    'data-testid', 'data-test', 'data-cy', 'data-qa',
  ]);
  clone.querySelectorAll('*').forEach(el => {
    el.removeAttribute('style');
    el.removeAttribute('class');
    Array.from(el.attributes).forEach(a => {
      if (!keep.has(a.name)) el.removeAttribute(a.name);
    });
  });
  return clone.outerHTML;
};

window.__sqat_get_page_context__ = function () {
  const text = (el, n = 220) => el ? (el.textContent || '').replace(/\s+/g, ' ').trim().substring(0, n) : '';
  const best = el => {
    if (!el) return null;
    const tag = el.tagName.toLowerCase();
    for (const a of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
      const v = el.getAttribute(a);
      if (v) return `[${a}="${String(v).replace(/["\\]/g, '\\$&')}"]`;
    }
    if (el.getAttribute('aria-label')) return `${tag}[aria-label="${String(el.getAttribute('aria-label')).replace(/["\\]/g, '\\$&')}"]`;
    if (el.id && !/^[0-9a-f-]{16,}$/i.test(el.id)) return `#${window.CSS && CSS.escape ? CSS.escape(el.id) : el.id}`;
    if (el.name) return `${tag}[name="${String(el.name).replace(/["\\]/g, '\\$&')}"]`;
    return null;
  };
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4')).map(h => ({ level: parseInt(h.tagName[1], 10), text: text(h) })).filter(h => h.text);
  const forms = Array.from(document.querySelectorAll('form')).map(form => ({
    action: form.getAttribute('action'),
    method: form.method || 'get',
    submit_text: text(form.querySelector('[type="submit"],button:not([type="button"])')),
    fields: Array.from(form.querySelectorAll('input:not([type="hidden"]),select,textarea')).map(f => ({
      tag: f.tagName.toLowerCase(),
      type: f.type || null,
      name: f.name || null,
      label: text(document.querySelector(`label[for="${String(f.id || '').replace(/["\\]/g, '\\$&')}"]`)),
      placeholder: f.placeholder || null,
      required: f.required || false,
      selector: best(f),
      options: f.tagName.toLowerCase() === 'select' ? Array.from(f.options || []).map(o => ({ value: o.value, label: text(o), selected: o.selected })) : [],
    })),
  }));
  const nav_links = Array.from(document.querySelectorAll('nav a,[role="navigation"] a,header a')).map(a => ({ text: text(a), href: a.getAttribute('href'), selector: best(a) })).filter(l => l.text && l.href).slice(0, 50);
  const buttons = Array.from(document.querySelectorAll('button,[role="button"]')).filter(b => {
    const r = b.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && !b.disabled;
  }).map(b => ({ text: text(b), selector: best(b), aria_label: b.getAttribute('aria-label') })).filter(b => b.text || b.aria_label).slice(0, 50);
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[role="alertdialog"],dialog[open]')).filter(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }).map(el => ({ title: text(el.querySelector('[aria-labelledby],h1,h2,h3')), text: text(el, 350) }));
  return { url: window.location.href, title: document.title, headings, forms, nav_links, buttons, dialogs };
};

window.__sqat_classify_snapshot_kind__ = function (actionType) {
  // Called by Python push_step() after an action to determine semantic snapshot_kind.
  const hasValidationError = !!(
    document.querySelector('[role="alert"]') ||
    document.querySelector('[aria-invalid="true"]') ||
    document.querySelector('.error, .is-error, .has-error, [class*="error"]')
  );
  const hasModal = !!document.querySelector('[role="dialog"]:not([aria-hidden="true"])');
  if (hasValidationError && actionType === 'submit') return 'validation_error';
  if (hasModal) return 'modal_state';
  return 'after_action';
};

window.__sqat_get_assertion_candidates__ = function () {
  const out = [];
  const seen = new Set();
  const text = (el, n = 260) => el ? (el.textContent || '').replace(/\s+/g, ' ').trim().substring(0, n) : '';
  const attr = (el, name) => el && el.getAttribute ? el.getAttribute(name) : null;
  const esc = v => String(v || '').replace(/["\\]/g, '\\$&');
  const stableId = id => !!id && !/^[0-9a-f-]{16,}$/i.test(id);
  const visible = el => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  // Elements to exclude: body-level containers and giant layout elements (>60% viewport)
  const EXCLUDED_IDS = new Set(['__next', '__nuxt', 'app', 'root', '__app']);
  const EXCLUDED_TAGS = new Set(['body', 'html', 'main']);
  const viewportArea = window.innerWidth * window.innerHeight;
  const isExcluded = el => {
    if (!el || !el.tagName) return true;
    const tag = el.tagName.toLowerCase();
    if (EXCLUDED_TAGS.has(tag)) return true;
    const id = (el.id || '').toLowerCase().replace(/^#/, '');
    if (EXCLUDED_IDS.has(id)) return true;
    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
    if (r && (r.width * r.height) > viewportArea * 0.6) return true;
    return false;
  };
  const selector = el => {
    if (!el || !el.tagName) return null;
    const tag = el.tagName.toLowerCase();
    for (const a of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
      const v = attr(el, a);
      if (v) return `[${a}="${esc(v)}"]`;
    }
    if (attr(el, 'aria-label')) return `${tag}[aria-label="${esc(attr(el, 'aria-label'))}"]`;
    if (el.id && stableId(el.id)) return `#${window.CSS && CSS.escape ? CSS.escape(el.id) : esc(el.id)}`;
    if (el.name) return `${tag}[name="${esc(el.name)}"]`;
    return null;
  };
  const hasStableSelector = el => selector(el) !== null;
  const push = (kind, el, confidence = 0.75, extra = {}) => {
    if (!visible(el) || isExcluded(el)) return;
    const value = text(el);
    if (!value) return;
    const sel = selector(el);
    // Set confidence=0.0 for any candidate without a stable selector
    const adjustedConfidence = sel ? confidence : 0.0;
    const key = `${kind}|${sel || ''}|${value}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ kind, selector: sel, text: value, confidence: adjustedConfidence, ...extra });
  };

  // ui_text: headings
  document.querySelectorAll('h1,h2,h3,h4,[role="heading"]').forEach(el => push('ui_text', el, 0.9, { source: 'heading' }));
  // error_message: alerts and aria-invalid messages
  document.querySelectorAll('[role="alert"],[role="status"],[aria-live]').forEach(el => push('error_message', el, 0.95, { source: 'alert' }));
  document.querySelectorAll('[aria-invalid="true"]').forEach(el => push('error_message', el, 0.85, { source: 'aria_invalid' }));
  // element_visible: stable data-testid elements
  document.querySelectorAll('[data-testid],[data-test],[data-cy],[data-qa]').forEach(el => {
    const key = `${attr(el, 'data-testid') || attr(el, 'data-test') || attr(el, 'data-cy') || attr(el, 'data-qa')}`.toLowerCase();
    if (/error|alert|success|complete|confirm|message|badge|count|total|title|name|price|summary|empty/.test(key)) {
      push('element_visible', el, 0.9, { source: 'stable_attr', data_key: key });
    }
  });
  // count_check: badge/count elements
  document.querySelectorAll('.badge,.count,[class*="badge"],[class*="count"]').forEach(el => push('count_check', el, 0.7, { source: 'badge' }));
  // list_items
  document.querySelectorAll('ul,ol,[role="list"]').forEach((list, listIndex) => {
    if (isExcluded(list)) return;
    const items = Array.from(list.querySelectorAll('li,[role="listitem"]')).filter(visible).map(item => text(item, 180)).filter(Boolean).slice(0, 40);
    if (items.length) {
      const sel = selector(list);
      const key = `list_items|${sel || ''}|${items.slice(0, 3).join('|')}`;
      if (!seen.has(key)) {
        seen.add(key);
        out.push({ kind: 'list_items', selector: sel, text: items.join(' | '), confidence: sel ? 0.75 : 0.0, source: 'list', item_count: items.length, list_index: listIndex, items });
      }
    }
  });
  // table_rows
  document.querySelectorAll('table,[role="table"],[role="grid"]').forEach((table, tableIndex) => {
    if (isExcluded(table)) return;
    const rows = Array.from(table.querySelectorAll('tr,[role="row"]')).filter(visible).map(row => text(row, 220)).filter(Boolean).slice(0, 40);
    if (rows.length) {
      const sel = selector(table);
      const key = `table_rows|${sel || ''}|${rows.slice(0, 2).join('|')}`;
      if (!seen.has(key)) {
        seen.add(key);
        out.push({ kind: 'table_rows', selector: sel, text: rows.join(' | '), confidence: sel ? 0.8 : 0.0, source: 'table', row_count: rows.length, table_index: tableIndex, rows });
      }
    }
  });
  // url_match
  const urlPath = window.location.pathname + window.location.search;
  if (urlPath) {
    out.push({ kind: 'url_match', selector: null, text: urlPath, confidence: 0.8, source: 'url' });
  }
  return out.slice(0, 120);
};

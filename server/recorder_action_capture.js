(function () {
  if (window.__sqat_installed__) return;
  window.__sqat_installed__ = true;

  const STORE_PASSWORD_VALUES = window.__sqat_store_password_values__ === true;
  const focusState = new WeakMap();
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
      if (v) return { selector: `[${a}="${esc(v)}"]`, stability: 'high' };
    }
    const aria = attr(el, 'aria-label');
    if (aria) return { selector: `${tag}[aria-label="${esc(aria)}"]`, stability: 'high' };
    if (el.id && stableId(el.id)) return { selector: `#${css(el.id)}`, stability: 'high' };
    if (el.name) return { selector: `${tag}[name="${esc(el.name)}"]`, stability: 'medium' };
    if (el.placeholder) return { selector: `${tag}[placeholder="${esc(el.placeholder)}"]`, stability: 'medium' };
    return { selector: tag, stability: 'low' };
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
  function snapshot(el) {
    const s = selector(el);
    const r = role(el);
    const n = nameOf(el);
    const loc = locator(el, n, r);
    return {
      selector: s?.selector || null,
      selector_stability: s?.stability || 'low',
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
    return el && el.tagName && !['HTML', 'BODY'].includes(el.tagName) && !(el.closest && el.closest('#__sqat_capture_indicator__'));
  }
  function action(type, el, extra = {}) {
    const snap = snapshot(el);
    return {
      type,
      selector: snap.selector,
      stability: snap.selector_stability,
      playwrightLocator: snap.playwright_locator,
      text: snap.visible_text,
      accessibleName: snap.accessible_name,
      role: snap.role,
      label: snap.label,
      inputType: snap.type,
      elementType: snap.tag,
      value: extra.value,
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

  document.addEventListener('focusin', e => {
    if (ok(e.target)) focusState.set(e.target, state(e.target));
  }, true);
  document.addEventListener('pointerdown', e => {
    if (ok(e.target)) focusState.set(e.target, state(e.target));
  }, true);
  document.addEventListener('click', e => {
    if (!ok(e.target)) return;
    const el = e.target;
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
    const t = (el.type || '').toLowerCase();
    const redacted = t === 'password' && !STORE_PASSWORD_VALUES;
    const kind = tag === 'select' ? 'select' : t === 'range' ? 'slide' : ['checkbox', 'radio'].includes(t) ? (el.checked ? 'check' : 'uncheck') : 'fill';
    window.__sqat_action__(action(kind, el, {
      value: redacted ? null : (el.value || '').substring(0, 500),
      beforeState: focusState.get(el) || null,
      afterState: state(el),
      event: { committed: true },
    })).catch(() => {});
  }, true);
  document.addEventListener('keydown', e => {
    if (!['Enter', 'Tab'].includes(e.key) || !ok(e.target)) return;
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
    el.innerHTML = '<div>CAM</div>';
    const style = document.createElement('style');
    style.id = '__sqat_capture_indicator_style__';
    style.textContent = '#__sqat_capture_indicator__{position:fixed;inset:0;pointer-events:none;z-index:2147483647;box-shadow:inset 0 0 0 4px rgba(37,99,235,.95),inset 0 0 38px rgba(37,99,235,.55);opacity:0;transition:opacity 120ms ease}#__sqat_capture_indicator__.on{opacity:1}#__sqat_capture_indicator__ div{position:fixed;right:18px;bottom:18px;width:46px;height:46px;border-radius:999px;background:#2563eb;color:white;font:700 11px/46px system-ui,sans-serif;text-align:center;box-shadow:0 10px 24px rgba(37,99,235,.35)}';
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
    return { selector: tag, selector_stability: 'low' };
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
      if (rect.width === 0 || rect.height === 0 || style.display === 'none' || style.visibility === 'hidden') return;
      const b = best(el);
      out.push({
        tag: el.tagName.toLowerCase(),
        type: el.type || null,
        role: role(el),
        text: text(el.innerText || el.textContent),
        accessible_name: name(el),
        label: text(document.querySelector(`label[for="${esc(el.id)}"]`)?.textContent),
        placeholder: el.placeholder || null,
        name: el.name || null,
        id: el.id || null,
        class: attr(el, 'class'),
        href: el.tagName.toLowerCase() === 'a' ? attr(el, 'href') : null,
        disabled: el.disabled === true || attr(el, 'aria-disabled') === 'true',
        required: el.required === true || attr(el, 'aria-required') === 'true',
        checked: typeof el.checked === 'boolean' ? el.checked : null,
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

# Design System

Extracted from admin panel T8 prototype (2026-06-30).

## Colors

```
primary:     #2563eb  (blue-600)   — buttons, links, slug text, active tab
primary-hover: #1d4ed8 (blue-700)
success:     #22c55e  (green-500)  — Active badge, webhook registered, toggle on
warning:     #eab308  (yellow-500) — webhook mismatch badge
danger:      #ef4444  (red-400)    — webhook error badge
danger-action: #dc2626 (red-600)  — delete button, error text
neutral-900: #111827              — headings
neutral-700: #374151              — label text
neutral-600: #4b5563              — secondary text, cancel button
neutral-500: #6b7280              — placeholder text
neutral-400: #9ca3af              — table cell secondary text
neutral-300: #d1d5db              — input border
neutral-200: #e5e7eb              — dividers, card border
neutral-100: #f3f4f6              — table header bg, badge bg
neutral-50:  #f9fafb              — page background
white:       #ffffff              — card bg, input bg
```

## Typography

```
font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif
  (Tailwind default font-sans — no custom font loaded)

font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace
  (used for: slug column, API key display, delete confirm input)

sizes:
  xs:   0.75rem / 12px  — table cells, labels, badges, timestamps
  sm:   0.875rem / 14px — buttons, inputs, body text
  base: 1rem / 16px     — (not commonly used in admin UI)
  lg:   1.125rem / 18px — page headings (h2)
  xl:   1.25rem / 20px  — (not used)

weights:
  normal:  400 — body text, input values
  medium:  500 — button labels, badge text
  semibold: 600 — nav brand, headings, slug in edit panel header
```

## Spacing Scale

Tailwind default (rem-based):
```
1  → 0.25rem / 4px
1.5 → 0.375rem / 6px
2  → 0.5rem / 8px
3  → 0.75rem / 12px
4  → 1rem / 16px    — standard horizontal padding
5  → 1.25rem / 20px — card padding, section gaps
6  → 1.5rem / 24px
```

## Border Radius

```
sm:  0.25rem / 4px  — badges (rounded-full for pill shape)
md:  0.5rem / 8px   — inputs (.field)
lg:  0.5rem / 8px   — buttons
xl:  0.75rem / 12px — cards, table container
2xl: 1rem / 16px    — login modal
```

## Shadows

```
sm: 0 1px 2px 0 rgb(0 0 0 / 0.05)   — toggle knob
md: 0 4px 6px -1px rgb(0 0 0 / 0.1) — (not used)
```

## Components

### Badges (status pills)
```
Active:   bg-green-100  text-green-700   px-2 py-0.5 rounded-full text-xs font-medium
Inactive: bg-red-100    text-red-700     px-2 py-0.5 rounded-full text-xs font-medium
free:     bg-gray-100   text-gray-600    px-2 py-0.5 rounded-full text-xs font-medium
basic:    bg-blue-100   text-blue-700    px-2 py-0.5 rounded-full text-xs font-medium
pro:      bg-purple-100 text-purple-700  px-2 py-0.5 rounded-full text-xs font-medium
```

### Webhook dot badges (CSS dots, no emoji)
```
registered: w-2 h-2 rounded-full bg-green-500
mismatch:   w-2 h-2 rounded-full bg-yellow-500
unknown:    w-2 h-2 rounded-full bg-gray-400
error:      w-2 h-2 rounded-full bg-red-400
checking:   SVG spinner animate-spin w-3 h-3 text-blue-500
```

### Input field (.field)
```css
width: 100%;
padding: 0.375rem 0.75rem;
border: 1px solid #d1d5db;
border-radius: 0.5rem;
font-size: 0.875rem;
background: white;
focus: ring-2 ring-blue-500, border-transparent
```

### Toggle switch (HTML checkbox + Tailwind peer)
```html
<label class="flex items-center gap-3 cursor-pointer">
  <div class="relative">
    <input type="checkbox" class="sr-only peer">
    <div class="w-11 h-6 bg-gray-200 rounded-full peer-checked:bg-green-500 transition-colors duration-200"></div>
    <div class="absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow
                transition-transform duration-200 peer-checked:translate-x-5"></div>
  </div>
  <span class="text-sm text-gray-700">Label text</span>
</label>
```

### Card
```
bg-white border border-gray-200 rounded-xl overflow-hidden
```

### Primary button
```
bg-blue-600 text-white text-sm px-4 py-1.5 rounded-lg hover:bg-blue-700
disabled:opacity-40 transition-colors
```

### Secondary button
```
text-gray-600 text-sm px-3 py-1.5 rounded-lg hover:bg-gray-100 transition-colors
```

### Ghost danger button
```
text-red-600 border border-red-200 text-sm px-3 py-1.5 rounded-lg hover:bg-red-50
```

## Responsive Breakpoints

```
sm: 640px  — 2-col form grid, show Expertise + Webhook columns
md: 768px  — show Created column
lg: 1024px — (no current change)
xl: 1280px — (no current change)
max-width: max-w-5xl mx-auto (1024px effective content width)
```

## Animations

```
transition-colors: 150ms ease    — button/badge hover, toggle track
transition-transform: 200ms ease — toggle knob, chevron rotate, slide-down
flash-green: 2s — save success row highlight
row-out: 200ms — delete row collapse
```

## Libraries

```
Tailwind CSS:  CDN (https://cdn.tailwindcss.com) — utility classes
Alpine.js:     v3.14.1 CDN — reactive SPA directives
```

## Conventions

- **No emoji in UI** — use CSS dot badges, SVG icons, or text labels
- **Masked inputs** — `type="password"` with reveal eye button; NEVER pre-fill secrets
- **ARIA labels** — all masked inputs, reveal buttons, webhook refresh, delete confirm
- **Touch targets** — minimum 44×44px; wrap small icons in `<button class="p-2.5">`
- **Keyboard nav** — `tabindex="0"` on `<tr>`, `@keydown.enter` to expand, Escape to close
- **Error pattern** — `bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700`
- **Success flash** — `@keyframes flash-green` 2s on saved row

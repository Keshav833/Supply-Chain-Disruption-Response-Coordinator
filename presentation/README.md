# Supply Chain Disruption Response Coordinator — Presentation Deck

## Overview

This folder contains a **static, offline-capable HTML/CSS/JavaScript slide deck** for the Supply Chain Disruption Response Coordinator project (CSE476, Lovely Professional University).

## Files

- **index.html** — Main presentation (8 core slides + 5 appendix slides)
- **styles.css** — All styling and responsive design (light/dark theme)
- **app.js** — Navigation, keyboard/touch controls, speaker notes, theme toggle
- **data.json** — All numbers, scenarios, decisions, and execution trace (pulled from repo)
- **README.md** — This file

## How to Use

### Quick Start

Double-click **index.html** to open in your default browser. The deck is fully functional offline.

### Navigation

| Action | Control |
|--------|---------|
| Next slide | Right Arrow, Space, or Next button |
| Previous slide | Left Arrow or Prev button |
| Jump to slide | Number key (1–9, 0 = slide 10) or click dot |
| Go to start | Home key |
| Go to end | End key |
| Fullscreen | F key |
| Overview grid | O key (click any slide to exit) |
| Speaker notes | N key (shows side panel) |
| Toggle theme | T key (light ↔ dark) |
| Swipe (touch) | Left swipe = next, right swipe = prev |

### Speaker Notes

Press **N** to toggle the speaker notes panel on the right. Notes are ~80–120 words per slide and are also visible in print (see below).

### Presentation Mode

Press **F** for fullscreen. Use arrow keys or space to navigate. Exit with ESC or F again.

### Export to PDF

1. Press **Ctrl+P** to open Print dialog
2. Set:
   - **Layout:** Landscape
   - **Background graphics:** ON
   - **Destination:** Save as PDF
3. Click **Save**

Each slide will be one page. Speaker notes are included if visible.

## Slide Structure

### Core Slides (1–8)

1. **Title** — Project name, course, university, author, hook line
2. **Problem & Objective** — Why manual + unconstrained LLM fail; our goal
3. **The Five Agents** — Role, tools, blackboard section for each agent
4. **Architecture** — Blackboard diagram with 6 design principles
5. **One Run, Step by Step** — Real INC-1001 execution trace (animated timeline)
6. **Design Strengths** — 8 key architecture decisions and why they matter
7. **Testing & Failure Handling** — 188 tests, 100% evaluation accuracy, failure scenarios
8. **Results, Limitations, Future Scope** — What we built, honest limits, roadmap

### Appendix Slides (9–16)

- **Slide A** — Tech stack with real versions from requirements.txt
- **Slide B** — Sample data rows from incidents, inventory, suppliers, routes
- **Slide C** — FastAPI endpoints and React dashboard overview
- **Slide D** — Requirement-to-evidence mapping (which files prove each requirement)
- **Slide E** — Viva Q&A: 8 likely questions with grounded answers

## Customization

Edit the **CONFIG** block at the top of **app.js**:

```javascript
const CONFIG = {
    author_name: "Your Name",
    group_name: "Group X / Registration XXXXX",
};
```

These values appear on the title slide and are automatically filled.

## Data Source

All numbers on the slides come from **data.json**, which is generated from:

- **DEVELOPMENT_STATE.md** — Project status, test counts, phases
- **requirements.txt** — Exact package versions
- **supply_chain/schemas.py** — Agent contracts, decision types
- **supply_chain/orchestrator.py** — Blackboard flow, timeout, concurrent execution
- **supply_chain/agents/*.py** — Agent roles and tools
- **tests/fixtures/expected_outcomes.json** — 5 scenarios and expected decisions
- **scripts/evaluate.py** output — Evaluation metrics (100% accuracy)
- **INC-1001 live run trace** — Real 26-step execution timeline

None of the numbers are invented or approximate.

## Design Features

- **16:9 aspect ratio** scaled to fit window
- **Responsive:** Works on desktop, tablet, phone (controls adapt)
- **Light and dark themes** with smooth transitions
- **Respects prefers-reduced-motion** (no animations if enabled)
- **Accessible:** Semantic HTML, sufficient contrast, readable fonts (20px+ at 1920px)
- **Print-friendly:** One slide per landscape page, backgrounds on
- **Offline:** No CDN, no external fonts, no API calls
- **Keyboard-first:** Arrow keys, space, number keys, F, O, N, T
- **Touch-friendly:** Swipe navigation on mobile

## Browser Support

- Chrome/Edge 90+
- Firefox 88+
- Safari 14+
- Mobile browsers (iOS Safari, Chrome Mobile)

## Speaker Notes

Each slide has speaker notes (~80–120 words) covering:
- Context and reasoning
- Key points to emphasize
- How to handle Q&A
- Connections to code

Access via **N** key or in print view.

## URL Parameters

- `?notes=1` — Show speaker notes in print (Ctrl+P)
- No parameters needed for normal presentation

## Accessibility

- Semantic HTML (`<main>`, `<nav>`, `<article>`)
- ARIA labels where needed
- Sufficient color contrast (WCAG AA)
- Font sizes scaled appropriately
- Keyboard navigation fully supported
- Screen reader compatible

## Notes

- The deck is **static** — no build step, no dependencies, no webpack
- All styling is **inline or CSS** (no external stylesheets loaded from CDN)
- JavaScript is **vanilla** (no jQuery, React, Vue, etc.)
- Animation respects **prefers-reduced-motion** media query
- Theme preference is **persisted** in localStorage

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Styling looks wrong | Clear browser cache (Ctrl+Shift+Delete) or open in private/incognito |
| Speaker notes not showing | Press **N** key; check that "Show Details" is not hiding them |
| Print layout broken | Use Landscape orientation and enable "Background graphics" in print settings |
| Dark theme not working | Press **T** to toggle; check localStorage (browser dev tools) |
| Navigation sluggish | Reduce browser extensions; refresh page |

## Credits

- **Project:** Supply Chain Disruption Response Coordinator
- **Course:** CSE476 — Agentic AI and Intelligent Automation
- **University:** Lovely Professional University
- **Author:** Keshav Chauhan
- **Presentation:** HTML5, CSS3, Vanilla JS (zero dependencies)

---

**Version:** 1.0  
**Last Updated:** 2026-11-25  
**Status:** Complete, ready for presentation

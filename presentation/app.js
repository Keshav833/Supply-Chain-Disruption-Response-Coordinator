/**
 * Supply Chain Disruption Response Coordinator - Presentation Deck
 * Static, offline-capable slide deck with keyboard/mouse/touch controls
 */

// ============================================================================
// CONFIG - Edit these values to personalize the presentation
// ============================================================================

const CONFIG = {
    author_name: "Keshav Chauhan",
    group_name: "(edit in app.js CONFIG)",  // e.g., "Group 2 / Reg. 12345"
};

// ============================================================================
// GLOBAL STATE
// ============================================================================

let currentSlide = 1;
let totalSlides = 1;
let isOverviewMode = false;
let isDarkTheme = window.matchMedia("(prefers-color-scheme: dark)").matches;
let showNotes = false;

// ============================================================================
// INITIALIZATION
// ============================================================================

document.addEventListener("DOMContentLoaded", () => {
    initializeConfig();
    countSlides();
    renderDots();
    attachEventListeners();
    goToSlide(1);
    updateTheme();
    loadThemePreference();
});

function initializeConfig() {
    document.getElementById("author-name").textContent = CONFIG.author_name;
    document.getElementById("group-name").textContent = CONFIG.group_name;
}

function countSlides() {
    totalSlides = document.querySelectorAll(".slide").length;
    document.getElementById("slide-total").textContent = totalSlides;
}

function renderDots() {
    const container = document.getElementById("dots-container");
    for (let i = 1; i <= totalSlides; i++) {
        const dot = document.createElement("div");
        dot.className = "dot";
        dot.dataset.slide = i;
        dot.title = `Slide ${i}`;
        dot.addEventListener("click", () => goToSlide(i));
        container.appendChild(dot);
    }
}

function attachEventListeners() {
    document.getElementById("prev-btn").addEventListener("click", () => previousSlide());
    document.getElementById("next-btn").addEventListener("click", () => nextSlide());
    document.getElementById("close-notes").addEventListener("click", () => toggleNotes());

    document.addEventListener("keydown", handleKeyboard);
    document.addEventListener("keyup", handleKeyboardUp);
    document.addEventListener("touchstart", handleTouchStart);
    document.addEventListener("touchend", handleTouchEnd);
}

// ============================================================================
// SLIDE NAVIGATION
// ============================================================================

function goToSlide(n) {
    if (n < 1) n = totalSlides;
    if (n > totalSlides) n = 1;

    currentSlide = n;

    // Hide all slides
    document.querySelectorAll(".slide").forEach(slide => slide.classList.remove("active"));

    // Show current slide
    const currentSlideEl = document.querySelectorAll(".slide")[currentSlide - 1];
    currentSlideEl.classList.add("active");

    // Update counter and progress
    document.getElementById("slide-current").textContent = currentSlide;
    const progress = (currentSlide / totalSlides) * 100;
    document.getElementById("progress-fill").style.width = progress + "%";

    // Update dots
    document.querySelectorAll(".dot").forEach(dot => dot.classList.remove("active"));
    document.querySelector(`.dot[data-slide="${currentSlide}"]`).classList.add("active");

    // Update speaker notes
    const speakerNotesEl = currentSlideEl.querySelector(".speaker-notes");
    if (speakerNotesEl) {
        document.getElementById("notes-content").textContent = speakerNotesEl.textContent;
    }
}

function nextSlide() {
    if (!isOverviewMode) {
        goToSlide(currentSlide + 1);
    }
}

function previousSlide() {
    if (!isOverviewMode) {
        goToSlide(currentSlide - 1);
    }
}

// ============================================================================
// KEYBOARD CONTROLS
// ============================================================================

let spacePressed = false;

function handleKeyboard(event) {
    if (isOverviewMode && event.key !== "o" && event.key !== "O") {
        return;  // In overview, only 'o' works to exit
    }

    switch (event.key.toLowerCase()) {
        case "arrowleft":
            event.preventDefault();
            previousSlide();
            break;
        case "arrowright":
        case " ":
            event.preventDefault();
            if (!spacePressed) {
                nextSlide();
                spacePressed = true;
            }
            break;
        case "home":
            event.preventDefault();
            goToSlide(1);
            break;
        case "end":
            event.preventDefault();
            goToSlide(totalSlides);
            break;
        case "f":
            event.preventDefault();
            toggleFullscreen();
            break;
        case "o":
            event.preventDefault();
            toggleOverview();
            break;
        case "n":
            event.preventDefault();
            toggleNotes();
            break;
        case "t":
            event.preventDefault();
            toggleTheme();
            break;
        default:
            // Allow numeric input for slide jumping
            const num = parseInt(event.key);
            if (!isNaN(num) && num > 0 && num <= totalSlides) {
                event.preventDefault();
                goToSlide(num);
            }
    }
}

function handleKeyboardUp(event) {
    if (event.key === " ") {
        spacePressed = false;
    }
}

// ============================================================================
// TOUCH / SWIPE CONTROLS
// ============================================================================

let touchStartX = 0;
let touchEndX = 0;

function handleTouchStart(e) {
    touchStartX = e.changedTouches[0].screenX;
}

function handleTouchEnd(e) {
    touchEndX = e.changedTouches[0].screenX;
    const diff = touchStartX - touchEndX;

    if (Math.abs(diff) > 100) {  // Swipe threshold
        if (diff > 0) {
            nextSlide();  // Swipe left -> next
        } else {
            previousSlide();  // Swipe right -> prev
        }
    }
}

// ============================================================================
// FULLSCREEN
// ============================================================================

function toggleFullscreen() {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(err => {
            console.log("Fullscreen not available:", err.message);
        });
    } else {
        if (document.exitFullscreen) {
            document.exitFullscreen();
        }
    }
}

// ============================================================================
// OVERVIEW MODE
// ============================================================================

function toggleOverview() {
    isOverviewMode = !isOverviewMode;
    const deck = document.querySelector(".deck");

    if (isOverviewMode) {
        deck.classList.add("overview-mode");
        // Make all slides clickable
        document.querySelectorAll(".slide").forEach((slide, idx) => {
            slide.style.cursor = "pointer";
            slide.addEventListener("click", () => {
                isOverviewMode = false;
                deck.classList.remove("overview-mode");
                goToSlide(idx + 1);
            });
        });
    } else {
        deck.classList.remove("overview-mode");
    }
}

// ============================================================================
// SPEAKER NOTES
// ============================================================================

function toggleNotes() {
    showNotes = !showNotes;
    const panel = document.getElementById("notes-panel");
    if (showNotes) {
        panel.style.display = "flex";
    } else {
        panel.style.display = "none";
    }
}

// ============================================================================
// THEME
// ============================================================================

function updateTheme() {
    if (isDarkTheme) {
        document.body.classList.add("dark-theme");
        document.getElementById("theme-indicator").textContent = "🌙 Dark";
    } else {
        document.body.classList.remove("dark-theme");
        document.getElementById("theme-indicator").textContent = "☀️ Light";
    }
}

function toggleTheme() {
    isDarkTheme = !isDarkTheme;
    updateTheme();
    localStorage.setItem("deck-theme", isDarkTheme ? "dark" : "light");
}

function loadThemePreference() {
    const saved = localStorage.getItem("deck-theme");
    if (saved) {
        isDarkTheme = saved === "dark";
        updateTheme();
    }
}

// ============================================================================
// PRINT SUPPORT
// ============================================================================

// Print notes with ?notes=1 in URL
window.addEventListener("beforeprint", () => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("notes") === "1") {
        document.querySelectorAll(".speaker-notes").forEach(notes => {
            notes.style.display = "block";
        });
    }
});

// ============================================================================
// ACCESSIBILITY
// ============================================================================

// Announce slide changes to screen readers
function announceSlide() {
    const slide = document.querySelectorAll(".slide")[currentSlide - 1];
    const title = slide.querySelector("h1, h2");
    if (title) {
        const message = `Slide ${currentSlide} of ${totalSlides}: ${title.textContent}`;
        // In a real app, use aria-live region
        console.log(message);
    }
}

// Auto-announce on slide change (optional)
// Call this if you want screen reader feedback
// announceSlide();

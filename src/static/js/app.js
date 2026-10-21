/* ============================================================
   VIETNAM TRAVEL ASSISTANT - CLIENT LOGIC
   Features: AI Chatbot, Attraction Explorer, Itinerary Planner
   ============================================================ */

// Global State
const state = {
    currentTab: 'chat-tab',
    currentPage: 1,
    limit: 12,
    totalPages: 1,
    totalItems: 0,
    attractions: [],
    chatPois: {},
    itinerary: JSON.parse(localStorage.getItem('viet_travel_itinerary') || '[]'),
    activeDetailItem: null,
    currentDraft: null
};

// DOM Elements
const elements = {
    chatForm: document.getElementById('chat-form'),
    userInput: document.getElementById('user-input'),
    chatMessages: document.getElementById('chat-messages-container'),
    sendBtn: document.getElementById('send-btn'),
    
    // Tabs
    navItems: document.querySelectorAll('.nav-item[data-tab]'),
    tabPanes: document.querySelectorAll('.tab-pane'),
    currentTabTitle: document.getElementById('current-tab-title'),
    currentTabSubtitle: document.getElementById('current-tab-subtitle'),
    
    // Explore
    attractionsGrid: document.getElementById('attractions-grid'),
    exploreSearchInput: document.getElementById('explore-search-input'),
    districtFilter: document.getElementById('district-filter'),
    categoryFilter: document.getElementById('category-filter'),
    resetFilterBtn: document.getElementById('reset-filter-btn'),
    placesCountText: document.getElementById('places-count-text'),
    prevPageBtn: document.getElementById('prev-page-btn'),
    nextPageBtn: document.getElementById('next-page-btn'),
    pageIndicator: document.getElementById('page-indicator'),
    
    // Itinerary Local Stored
    itineraryCount: document.getElementById('itinerary-count'),
    itineraryTimeline: document.getElementById('itinerary-timeline'),
    itineraryEmptyState: document.getElementById('itinerary-empty-state'),
    clearItineraryBtn: document.getElementById('clear-itinerary-btn'),
    btnPlanFromSelected: document.getElementById('btn-plan-from-selected'),

    // AI Draft Planner (Phase I)
    planDraftForm: document.getElementById('plan-draft-form'),
    draftDateInput: document.getElementById('draft-date'),
    draftAreaInput: document.getElementById('draft-area'),
    draftQueryInput: document.getElementById('draft-query'),
    draftMaxStopsInput: document.getElementById('draft-max-stops'),
    draftPreferencesInput: document.getElementById('draft-preferences'),
    btnPlanDraft: document.getElementById('btn-submit-draft'),

    draftResultSection: document.getElementById('draft-result-section'),
    draftLoading: document.getElementById('draft-loading'),
    draftAlert: document.getElementById('draft-alert'),
    draftContent: document.getElementById('draft-content'),
    draftIdText: document.getElementById('draft-id-text'),
    draftDateText: document.getElementById('draft-date-text'),
    draftStopsCount: document.getElementById('draft-stops-count'),
    draftDistanceText: document.getElementById('draft-distance-total'),
    draftDurationText: document.getElementById('draft-time-span'),
    draftWarningsContainer: document.getElementById('draft-warnings-container'),
    draftItemsTimeline: document.getElementById('draft-items-timeline'),
    confirmActionsBar: document.getElementById('confirm-actions-bar'),
    confirmStatusAlert: document.getElementById('confirm-status-alert'),
    btnDiscardDraft: document.getElementById('btn-discard-draft'),
    btnConfirmDraft: document.getElementById('btn-confirm-draft'),
    
    // Modal
    modal: document.getElementById('detail-modal'),
    modalCloseBtn: document.getElementById('modal-close-btn'),
    modalTitle: document.getElementById('modal-title'),
    modalDesc: document.getElementById('modal-desc'),
    modalCategory: document.getElementById('modal-category'),
    modalDistrict: document.getElementById('modal-district'),
    modalAddress: document.getElementById('modal-address'),
    modalHours: document.getElementById('modal-hours'),
    modalPrice: document.getElementById('modal-price'),
    modalRating: document.getElementById('modal-rating'),
    modalAddItineraryBtn: document.getElementById('modal-add-itinerary-btn'),
    
    // Theme
    themeToggleBtn: document.getElementById('theme-toggle-btn'),

    // Authentication for saving confirmed itineraries
    authModal: document.getElementById('auth-modal'),
    authForm: document.getElementById('auth-form'),
    authEmail: document.getElementById('auth-email'),
    authPassword: document.getElementById('auth-password'),
    authError: document.getElementById('auth-error'),
    authOpenBtn: document.getElementById('auth-open-btn'),
    authLogoutBtn: document.getElementById('auth-logout-btn'),
    authUserLabel: document.getElementById('auth-user-label'),
    authCloseBtn: document.getElementById('auth-close-btn'),
    authCancelBtn: document.getElementById('auth-cancel-btn'),
    authSubmitBtn: document.getElementById('auth-submit-btn')
};

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    setupNavigation();
    setupChat();
    setupExplorer();
    setupItinerary();
    initDraftPlanner();
    setupModal();
    setupAuth();
    updateItineraryBadge();
});

/* ============================================================
   1. THEME TOGGLE
   ============================================================ */
function initTheme() {
    let savedTheme = localStorage.getItem('viet_travel_theme');
    if (!savedTheme || savedTheme === 'light') {
        savedTheme = 'dark';
        localStorage.setItem('viet_travel_theme', 'dark');
    }
    document.documentElement.setAttribute('data-theme', savedTheme);
    
    elements.themeToggleBtn.addEventListener('click', () => {
        const currentTheme = document.documentElement.getAttribute('data-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', newTheme);
        localStorage.setItem('viet_travel_theme', newTheme);
    });
}

/* ============================================================
   2. TAB NAVIGATION
   ============================================================ */
function setupNavigation() {
    elements.navItems.forEach(item => {
        item.addEventListener('click', () => {
            const targetTab = item.getAttribute('data-tab');
            switchTab(targetTab);
        });
    });
}

function switchTab(tabId) {
    state.currentTab = tabId;
    
    // Update nav active classes
    elements.navItems.forEach(item => {
        if (item.getAttribute('data-tab') === tabId) {
            item.classList.add('active');
        } else {
            item.classList.remove('active');
        }
    });

    // Update tab visibility
    elements.tabPanes.forEach(pane => {
        if (pane.id === tabId) {
            pane.classList.add('active');
        } else {
            pane.classList.remove('active');
        }
    });

    // Update headers
    if (tabId === 'chat-tab') {
        elements.currentTabTitle.textContent = 'Trò chuyện';
        elements.currentTabSubtitle.textContent = 'Tìm địa điểm và lên lịch.';
    } else if (tabId === 'explore-tab') {
        elements.currentTabTitle.textContent = 'Địa điểm';
        elements.currentTabSubtitle.textContent = 'Tìm điểm đến.';
        if (state.attractions.length === 0) {
            fetchAttractions();
        }
    } else if (tabId === 'itinerary-tab') {
        elements.currentTabTitle.textContent = 'Lịch trình';
        elements.currentTabSubtitle.textContent = 'Xem và tạo lịch trình.';
        renderItinerary();
    }
}

/* ============================================================
   3. AI CHAT FUNCTIONALITY
   ============================================================ */
function setupChat() {
    elements.chatForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const query = elements.userInput.value.trim();
        if (!query) return;

        // Add user message
        addUserMessage(query);
        elements.userInput.value = '';

        // Add typing indicator
        const typingEl = addTypingIndicator();

        try {
            const webSearchCheckbox = document.getElementById('web-search-checkbox');
            const enableWebSearch = webSearchCheckbox ? webSearchCheckbox.checked : true;

            const res = await fetch('/api/travel-chatbot/search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    question: query,
                    enable_web_search: enableWebSearch
                })
            });

            const data = await res.json();
            typingEl.remove();

            if (data.status === 'success') {
                addBotResponse(data);
            } else {
                addBotSimpleMessage(data.response || 'Xin lỗi, em gặp chút trục trặc khi xử lý câu hỏi này.');
            }
        } catch (err) {
            typingEl.remove();
            console.error('Chat error:', err);
            addBotSimpleMessage('Không thể kết nối đến máy chủ. Anh vui lòng kiểm tra lại đường truyền nhé!');
        }
    });
}

function sendQuickPrompt(promptText) {
    elements.userInput.value = promptText;
    elements.chatForm.dispatchEvent(new Event('submit'));
}

function addUserMessage(text) {
    // Ẩn bảng chào mừng ban đầu khi người dùng bắt đầu cuộc trò chuyện
    const welcomeHero = document.getElementById('chat-welcome-hero');
    if (welcomeHero) {
        welcomeHero.style.display = 'none';
    }

    const msgEl = document.createElement('div');
    msgEl.className = 'message-wrapper user';
    msgEl.innerHTML = `
        <div class="message-avatar">
            <i class="fa-solid fa-user"></i>
        </div>
        <div class="message-bubble">
            <div class="message-header">
                <span class="sender-name">Bạn</span>
                <span class="message-time">${getTimeNow()}</span>
            </div>
            <div class="message-body">
                <p>${escapeHtml(text)}</p>
            </div>
        </div>
    `;
    elements.chatMessages.appendChild(msgEl);
    scrollToBottom();
}

function addTypingIndicator() {
    const typingEl = document.createElement('div');
    typingEl.className = 'message-wrapper bot';
    typingEl.innerHTML = `
        <div class="message-avatar">
            <i class="fa-solid fa-robot"></i>
        </div>
        <div class="message-bubble">
            <div class="typing-dots">
                <span></span>
                <span></span>
                <span></span>
            </div>
        </div>
    `;
    elements.chatMessages.appendChild(typingEl);
    scrollToBottom();
    return typingEl;
}

function addBotSimpleMessage(text) {
    const msgEl = document.createElement('div');
    msgEl.className = 'message-wrapper bot';
    msgEl.innerHTML = `
        <div class="message-avatar">
            <i class="fa-solid fa-robot"></i>
        </div>
        <div class="message-bubble">
            <div class="message-header">
                <span class="sender-name">VietTravel AI — Trợ lý Đặt Lịch & Chuyến Đi</span>
                <span class="message-time">${getTimeNow()}</span>
            </div>
            <div class="message-body">
                <p>${formatMarkdown(text)}</p>
            </div>
        </div>
    `;
    elements.chatMessages.appendChild(msgEl);
    scrollToBottom();
}

function addBotResponse(data) {
    const msgEl = document.createElement('div');
    msgEl.className = 'message-wrapper bot';
    
    // Huy hiệu AI engine đã được gỡ bỏ theo yêu cầu giao diện
    const pipelineBadgesHtml = '';

    // Xây dựng khối nguồn tham khảo từ Web nếu có
    let webSourcesHtml = '';
    if (data.web_sources && data.web_sources.length > 0) {
        webSourcesHtml = `
            <div class="web-grounding-box">
                <div class="web-grounding-title">
                    <i class="fa-solid fa-link"></i>
                    <span>Nguồn tra cứu Web thời gian thực:</span>
                </div>
                <div class="web-source-chips">
                    ${data.web_sources.map(src => `
                        <a href="${escapeHtml(src.uri)}" target="_blank" rel="noopener noreferrer" class="web-source-chip" title="${escapeHtml(src.title)}">
                            <i class="fa-solid fa-arrow-up-right-from-square"></i>
                            <span>${escapeHtml(src.title || 'Nguồn tin')}</span>
                        </a>
                    `).join('')}
                </div>
            </div>
        `;
    }

    let htmlContent = `
        <div class="message-avatar">
            <i class="fa-solid fa-robot"></i>
        </div>
        <div class="message-bubble" style="max-width: 100%;">
            <div class="message-header">
                <div class="sender-info">
                    <span class="sender-name">VietTravel AI — Trợ lý Đặt Lịch & Chuyến Đi</span>
                    <span class="message-time">${getTimeNow()}</span>
                </div>
                ${pipelineBadgesHtml}
            </div>
            <div class="message-body">
                <p>${formatMarkdown(data.response || 'Dưới đây là một số địa điểm gợi ý phù hợp nhất với bạn:')}</p>
            </div>
            ${webSourcesHtml}
    `;

    // Render cards for found search results
    if (data.search_results && data.search_results.length > 0) {
        htmlContent += `<div class="chat-results-container">`;
        data.search_results.slice(0, 6).forEach(loc => {
            const locId = String(loc.id || ('osm_' + Math.random().toString(36).substr(2, 9)));
            loc.id = locId;
            state.chatPois[locId] = loc;

            const mapUrl = loc.google_maps_url || (loc.latitude && loc.longitude ? `https://www.google.com/maps?q=${loc.latitude},${loc.longitude}` : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent((loc.ten_dia_diem || '') + ' ' + (loc.khu_vuc || 'Việt Nam'))}`);

            const defaultCardImg = "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=800&auto=format&fit=crop";
            const cardImgUrl = loc.image_url || defaultCardImg;
            const thumbHtml = `
                <div class="mini-card-thumb">
                    <img src="${escapeHtml(cardImgUrl)}" alt="${escapeHtml(loc.ten_dia_diem)}" loading="lazy" onerror="this.src='https://images.unsplash.com/photo-1469854523086-cc02fe5d8800?w=800&auto=format&fit=crop'">
                    <span class="thumb-badge"><i class="fa-solid fa-location-dot"></i> ${escapeHtml(loc.khu_vuc || 'Việt Nam')}</span>
                </div>
            `;

            htmlContent += `
                <div class="mini-card">
                    ${thumbHtml}
                    <h4>${escapeHtml(loc.ten_dia_diem)}</h4>
                    <div class="mini-meta">
                        <span class="badge">${escapeHtml(loc.loai_dia_diem || 'Địa điểm')}</span>
                        <span class="badge badge-district">${escapeHtml(loc.khu_vuc || 'Việt Nam')}</span>
                        ${loc.danh_gia ? `<span class="badge badge-rating"><i class="fa-solid fa-star"></i> ${loc.danh_gia}</span>` : ''}
                    </div>
                    <p>${escapeHtml(loc.mo_ta || loc.dia_chi || '')}</p>
                    <div class="mini-card-actions">
                        <button class="btn-sm btn-outline-primary" onclick="openDetailById('${escapeHtml(locId)}')">
                            <i class="fa-solid fa-circle-info"></i> Chi tiết
                        </button>
                        <button class="btn-sm btn-primary" onclick="addToItineraryFromChat('${escapeHtml(locId)}')">
                            <i class="fa-solid fa-plus"></i> Thêm vào lịch
                        </button>
                        <a class="btn-sm btn-outline-secondary" href="${escapeHtml(mapUrl)}" target="_blank" rel="noopener noreferrer" title="Mở tọa độ thực tế trên Google Maps">
                            <i class="fa-solid fa-map-location-dot"></i> Bản đồ
                        </a>
                    </div>
                </div>
            `;
        });
        htmlContent += `</div>`;
    }

    htmlContent += `</div>`;
    msgEl.innerHTML = htmlContent;
    elements.chatMessages.appendChild(msgEl);
    scrollToBottom();
}

/* ============================================================
   4. EXPLORE ATTRACTIONS FUNCTIONALITY
   ============================================================ */
function setupExplorer() {
    elements.prevPageBtn.addEventListener('click', () => {
        if (state.currentPage > 1) {
            state.currentPage--;
            fetchAttractions();
        }
    });

    elements.nextPageBtn.addEventListener('click', () => {
        if (state.currentPage < state.totalPages) {
            state.currentPage++;
            fetchAttractions();
        }
    });

    elements.districtFilter.addEventListener('change', () => {
        state.currentPage = 1;
        fetchAttractions();
    });

    elements.categoryFilter.addEventListener('change', () => {
        state.currentPage = 1;
        fetchAttractions();
    });

    elements.resetFilterBtn.addEventListener('click', () => {
        elements.districtFilter.value = '';
        elements.categoryFilter.value = '';
        elements.exploreSearchInput.value = '';
        state.currentPage = 1;
        fetchAttractions();
    });

    let searchDebounce;
    elements.exploreSearchInput.addEventListener('input', () => {
        clearTimeout(searchDebounce);
        searchDebounce = setTimeout(() => {
            state.currentPage = 1;
            fetchAttractions();
        }, 300);
    });
}

async function fetchAttractions() {
    elements.attractionsGrid.innerHTML = `
        <div style="grid-column: 1/-1; text-align: center; padding: 60px 0; color: var(--text-muted);">
            <i class="fa-solid fa-spinner fa-spin" style="font-size: 32px; margin-bottom: 12px; color: var(--primary);"></i>
            <p>Đang tải danh sách địa điểm...</p>
        </div>
    `;

    try {
        const queryParams = new URLSearchParams({
            page: state.currentPage,
            limit: state.limit
        });

        const res = await fetch(`/api/travel-chatbot/metadata?${queryParams.toString()}`);
        const data = await res.json();

        if (data.status === 'success') {
            state.attractions = data.metadata || [];
            state.totalPages = data.total_pages || 1;
            state.totalItems = data.total || 0;

            renderAttractions();
            updatePaginationUI();
        }
    } catch (err) {
        console.error('Fetch attractions error:', err);
        elements.attractionsGrid.innerHTML = `
            <div style="grid-column: 1/-1; text-align: center; padding: 40px 0; color: var(--danger);">
                <i class="fa-solid fa-triangle-exclamation" style="font-size: 32px; margin-bottom: 8px;"></i>
                <p>Không thể tải dữ liệu địa điểm. Vui lòng thử lại.</p>
            </div>
        `;
    }
}

function renderAttractions() {
    let items = state.attractions;

    // Client-side filtering for search & selects
    const searchVal = elements.exploreSearchInput.value.toLowerCase().trim();
    const districtVal = elements.districtFilter.value;
    const catVal = elements.categoryFilter.value;

    if (searchVal) {
        items = items.filter(item => 
            (item.ten_dia_diem && item.ten_dia_diem.toLowerCase().includes(searchVal)) ||
            (item.mo_ta && item.mo_ta.toLowerCase().includes(searchVal)) ||
            (item.dia_chi && item.dia_chi.toLowerCase().includes(searchVal))
        );
    }
    if (districtVal) {
        items = items.filter(item => item.khu_vuc && item.khu_vuc.includes(districtVal));
    }
    if (catVal) {
        items = items.filter(item => item.loai_dia_diem && item.loai_dia_diem.includes(catVal));
    }

    if (items.length === 0) {
        elements.attractionsGrid.innerHTML = `
            <div style="grid-column: 1/-1; text-align: center; padding: 60px 0; color: var(--text-muted);">
                <i class="fa-solid fa-magnifying-glass-location" style="font-size: 40px; margin-bottom: 12px;"></i>
                <p>Không tìm thấy địa điểm nào khớp với tiêu chí tìm kiếm của bạn.</p>
            </div>
        `;
        return;
    }

    elements.attractionsGrid.innerHTML = items.map(item => `
        <div class="attraction-card">
            <div class="mini-card-thumb" style="height: 160px; margin-bottom: 0; border-radius: 12px 12px 0 0; border: none; border-bottom: 1px solid var(--border-color, rgba(255,255,255,0.08));">
                <img src="${escapeHtml(item.image_url || 'https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=800&auto=format&fit=crop')}" alt="${escapeHtml(item.ten_dia_diem)}" loading="lazy" onerror="this.src='https://images.unsplash.com/photo-1469854523086-cc02fe5d8800?w=800&auto=format&fit=crop'">
                <span class="thumb-badge"><i class="fa-solid fa-location-dot"></i> ${escapeHtml(item.khu_vuc || 'Việt Nam')}</span>
            </div>
            <div class="card-top" style="padding: 16px;">
                <div class="card-badges">
                    <span class="badge">${escapeHtml(item.loai_dia_diem || 'Địa điểm')}</span>
                    <span class="badge badge-district">${escapeHtml(item.khu_vuc || 'Việt Nam')}</span>
                    ${item.danh_gia ? `<span class="badge badge-rating"><i class="fa-solid fa-star"></i> ${item.danh_gia}</span>` : ''}
                </div>
                <h3 class="card-title">${escapeHtml(item.ten_dia_diem)}</h3>
                <p class="card-desc">${escapeHtml(item.mo_ta || 'Chưa có mô tả chi tiết.')}</p>
                <div class="card-meta-list">
                    <div class="card-meta-item">
                        <i class="fa-solid fa-location-dot"></i>
                        <span>${escapeHtml(item.dia_chi || 'Đang cập nhật')}</span>
                    </div>
                </div>
            </div>
            <div class="card-footer">
                <button class="btn-secondary" onclick='openModalWithItem(${JSON.stringify(item).replace(/'/g, "&#39;")})'>
                    <i class="fa-solid fa-circle-info"></i> Xem thêm
                </button>
                <button class="btn-primary" onclick='addToItinerary(${JSON.stringify(item).replace(/'/g, "&#39;")})'>
                    <i class="fa-solid fa-plus"></i> Thêm vào lịch
                </button>
            </div>
        </div>
    `).join('');
}

function updatePaginationUI() {
    elements.placesCountText.textContent = `Hiển thị trang ${state.currentPage} / ${state.totalPages} (Tổng ${state.totalItems} địa điểm)`;
    elements.pageIndicator.textContent = `Trang ${state.currentPage}`;
    elements.prevPageBtn.disabled = state.currentPage <= 1;
    elements.nextPageBtn.disabled = state.currentPage >= state.totalPages;
}

/* ============================================================
   5. ITINERARY PLANNER
   ============================================================ */
function setupItinerary() {
    initDraftPlanner();

    if (elements.clearItineraryBtn) {
        elements.clearItineraryBtn.addEventListener('click', () => {
            if (confirm('Bạn có chắc chắn muốn xóa toàn bộ lịch trình không?')) {
                state.itinerary = [];
                saveItinerary();
                renderItinerary();
            }
        });
    }
}

/* ============================================================
   5.1 AI DRAFT PLANNER & CONFIRMATION (PHASE I)
   ============================================================ */
const CITY_COORDS = {
    'vũng tàu': { lat: 10.3460, lng: 107.0843 },
    'vung tau': { lat: 10.3460, lng: 107.0843 },
    'hà nội': { lat: 21.0285, lng: 105.8542 },
    'ha noi': { lat: 21.0285, lng: 105.8542 },
    'hoàn kiếm': { lat: 21.0285, lng: 105.8542 },
    'hoan kiem': { lat: 21.0285, lng: 105.8542 },
    'ba đình': { lat: 21.0368, lng: 105.8347 },
    'ba dinh': { lat: 21.0368, lng: 105.8347 },
    'tây hồ': { lat: 21.0716, lng: 105.8230 },
    'tay ho': { lat: 21.0716, lng: 105.8230 },
    'hồ chí minh': { lat: 10.7769, lng: 106.7009 },
    'ho chi minh': { lat: 10.7769, lng: 106.7009 },
    'quận 1': { lat: 10.7769, lng: 106.7009 },
    'quan 1': { lat: 10.7769, lng: 106.7009 },
    'tphcm': { lat: 10.7769, lng: 106.7009 },
    'sài gòn': { lat: 10.7769, lng: 106.7009 },
    'đà nẵng': { lat: 16.0544, lng: 108.2022 },
    'da nang': { lat: 16.0544, lng: 108.2022 },
    'thái nguyên': { lat: 21.5942, lng: 105.8482 },
    'thai nguyen': { lat: 21.5942, lng: 105.8482 },
    'phổ yên': { lat: 21.4167, lng: 105.8667 },
    'pho yen': { lat: 21.4167, lng: 105.8667 },
    'sông công': { lat: 21.4833, lng: 105.8167 },
    'nha trang': { lat: 12.2388, lng: 109.1967 },
    'đà lạt': { lat: 11.9404, lng: 108.4583 },
    'da lat': { lat: 11.9404, lng: 108.4583 },
    'huế': { lat: 16.4637, lng: 107.5909 },
    'hue': { lat: 16.4637, lng: 107.5909 },
    'hội an': { lat: 15.8801, lng: 108.3380 },
    'hạ long': { lat: 20.9505, lng: 107.0734 },
    'quảng ninh': { lat: 20.9505, lng: 107.0734 },
    'sa pa': { lat: 22.3364, lng: 103.8438 },
    'phú quốc': { lat: 10.2899, lng: 103.9840 },
    'cần thơ': { lat: 10.0452, lng: 105.7469 },
    'ninh bình': { lat: 20.2506, lng: 105.9745 }
};

function resolveCityCoordinates(cityName) {
    if (!cityName) return null;
    const lower = cityName.trim().toLowerCase();
    for (const [key, coords] of Object.entries(CITY_COORDS)) {
        if (lower === key || lower.includes(key) || key.includes(lower)) {
            return coords;
        }
    }
    return null;
}

function initDraftPlanner() {
    if (!elements.planDraftForm) return;

    // Set today as minimum and default date
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    if (elements.draftDateInput) {
        elements.draftDateInput.min = today;
        if (!elements.draftDateInput.value) {
            elements.draftDateInput.value = today;
        }
    }

    // Auto geocoding on typing area
    if (elements.draftAreaInput) {
        elements.draftAreaInput.addEventListener('input', () => {
            const coords = resolveCityCoordinates(elements.draftAreaInput.value);
            if (coords) {
                const latInput = document.getElementById('draft-latitude');
                const lngInput = document.getElementById('draft-longitude');
                if (latInput) latInput.value = coords.lat;
                if (lngInput) lngInput.value = coords.lng;
            }
        });
    }

    // Fast city fill chips
    document.querySelectorAll('.city-fill-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            const area = chip.getAttribute('data-area');
            const lat = chip.getAttribute('data-lat');
            const lng = chip.getAttribute('data-lng');
            if (elements.draftAreaInput) elements.draftAreaInput.value = area;
            const latInput = document.getElementById('draft-latitude');
            const lngInput = document.getElementById('draft-longitude');
            if (latInput) latInput.value = lat;
            if (lngInput) lngInput.value = lng;
        });
    });

    // Plan from selected places button
    if (elements.btnPlanFromSelected) {
        elements.btnPlanFromSelected.addEventListener('click', handlePlanFromSelectedClick);
    }

    elements.planDraftForm.addEventListener('submit', handlePlanDraftSubmit);

    if (elements.btnConfirmDraft) {
        elements.btnConfirmDraft.addEventListener('click', handleConfirmDraftClick);
    }

    if (elements.btnDiscardDraft) {
        elements.btnDiscardDraft.addEventListener('click', handleDiscardDraftClick);
    }
}

function handlePlanFromSelectedClick() {
    if (!state.itinerary || state.itinerary.length === 0) {
        alert('Bạn chưa chọn địa điểm nào từ cuộc trò chuyện! Hãy qua mục Trò chuyện, tìm kiếm và bấm "+ Thêm vào lịch" ở các thẻ card nhé.');
        return;
    }

    const firstItem = state.itinerary[0];
    if (elements.draftAreaInput && !elements.draftAreaInput.value) {
        elements.draftAreaInput.value = firstItem.khu_vuc || 'Vũng Tàu';
    }

    const withCoords = state.itinerary.filter(i => i.latitude && i.longitude);
    const latInput = document.getElementById('draft-latitude');
    const lngInput = document.getElementById('draft-longitude');

    if (withCoords.length > 0) {
        const avgLat = (withCoords.reduce((acc, c) => acc + Number(c.latitude), 0) / withCoords.length).toFixed(4);
        const avgLng = (withCoords.reduce((acc, c) => acc + Number(c.longitude), 0) / withCoords.length).toFixed(4);
        if (latInput) latInput.value = avgLat;
        if (lngInput) lngInput.value = avgLng;
    } else {
        const coords = resolveCityCoordinates(firstItem.khu_vuc || elements.draftAreaInput?.value);
        if (coords) {
            if (latInput) latInput.value = coords.lat;
            if (lngInput) lngInput.value = coords.lng;
        }
    }

    if (elements.draftMaxStopsInput) {
        elements.draftMaxStopsInput.value = String(Math.min(5, Math.max(2, state.itinerary.length)));
    }

    // Scroll to form and submit
    if (elements.planDraftForm) {
        elements.planDraftForm.scrollIntoView({ behavior: 'smooth' });
        handlePlanDraftSubmit(new Event('submit'));
    }
}

async function handlePlanDraftSubmit(e) {
    if (e && e.preventDefault) e.preventDefault();
    hideDraftAlert();
    hideConfirmAlert();

    const selected_date = elements.draftDateInput ? elements.draftDateInput.value : '';
    if (!selected_date) {
        showDraftAlert('Vui lòng chọn ngày khởi hành (định dạng YYYY-MM-DD).', 'warning');
        return;
    }

    let area = elements.draftAreaInput ? elements.draftAreaInput.value.trim() : '';
    const query = elements.draftQueryInput ? elements.draftQueryInput.value.trim() : '';
    let latitudeText = document.getElementById('draft-latitude')?.value.trim() || '';
    let longitudeText = document.getElementById('draft-longitude')?.value.trim() || '';
    const radiusText = document.getElementById('draft-radius')?.value || '5';
    const maxStopsVal = elements.draftMaxStopsInput ? parseInt(elements.draftMaxStopsInput.value, 10) : 3;
    const preferencesVal = elements.draftPreferencesInput ? elements.draftPreferencesInput.value.trim() : '';

    // If both area and query are empty, but user already selected places in list
    if (!area && !query && state.itinerary && state.itinerary.length > 0) {
        area = state.itinerary[0].khu_vuc || 'Vũng Tàu';
        if (elements.draftAreaInput) elements.draftAreaInput.value = area;
    }

    // Auto-detect coordinates from area name if coords are not entered
    if (area && (!latitudeText || !longitudeText)) {
        const autoCoords = resolveCityCoordinates(area);
        if (autoCoords) {
            latitudeText = String(autoCoords.lat);
            longitudeText = String(autoCoords.lng);
            const latInput = document.getElementById('draft-latitude');
            const lngInput = document.getElementById('draft-longitude');
            if (latInput) latInput.value = latitudeText;
            if (lngInput) lngInput.value = longitudeText;
        }
    }

    if (!area && !query) {
        showDraftAlert('Vui lòng nhập khu vực (ví dụ: Vũng Tàu, Hoàn Kiếm, Đà Nẵng...) hoặc từ khóa địa điểm.', 'warning');
        return;
    }

    if (Boolean(latitudeText) !== Boolean(longitudeText)) {
        showDraftAlert('Hãy nhập cả vĩ độ và kinh độ, hoặc chọn nhanh tỉnh/thành phố để hệ thống tự điền.', 'warning');
        return;
    }

    const latitude = latitudeText ? Number(latitudeText) : null;
    const longitude = longitudeText ? Number(longitudeText) : null;
    const radiusKm = Number(radiusText);
    if (latitudeText && (!Number.isFinite(latitude) || latitude < 8 || latitude > 24)) {
        showDraftAlert('Vĩ độ phải nằm trong khoảng 8 đến 24 (Việt Nam).', 'warning');
        return;
    }
    if (longitudeText && (!Number.isFinite(longitude) || longitude < 102 || longitude > 110)) {
        showDraftAlert('Kinh độ phải nằm trong khoảng 102 đến 110 (Việt Nam).', 'warning');
        return;
    }
    if (!Number.isFinite(radiusKm) || radiusKm <= 0 || radiusKm > 100) {
        showDraftAlert('Bán kính tìm kiếm phải lớn hơn 0 và không quá 100 km.', 'warning');
        return;
    }
    if (area && latitude === null) {
        showDraftAlert('Hãy bấm vào một trong các nút chọn nhanh (Vũng Tàu, Hà Nội, TP.HCM...) hoặc nhập tọa độ để hệ thống lọc chính xác khu vực.', 'warning');
        return;
    }

    const payload = {
        selected_date: selected_date,
        max_stops: isNaN(maxStopsVal) || maxStopsVal < 1 ? 3 : maxStopsVal
    };
    if (area) payload.area = area;
    if (query) payload.query = query;
    if (latitude !== null) {
        payload.latitude = latitude;
        payload.longitude = longitude;
        payload.radius_km = radiusKm;
    }
    if (preferencesVal) payload.preferences = preferencesVal;

    // Display loading state
    if (elements.draftResultSection) elements.draftResultSection.style.display = 'block';
    if (elements.draftLoading) elements.draftLoading.style.display = 'flex';
    if (elements.draftContent) elements.draftContent.style.display = 'none';
    if (elements.btnPlanDraft) {
        elements.btnPlanDraft.disabled = true;
        elements.btnPlanDraft.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Đang lập lịch nháp...';
    }

    try {
        const response = await fetch('/api/itinerary/multi-agent/plan-draft', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });

        const data = await response.json().catch(() => ({}));

        if (response.ok && data.status === 'success') {
            state.currentDraft = data;
            renderDraftItinerary(data);
            if (elements.draftLoading) elements.draftLoading.style.display = 'none';
            if (elements.draftContent) elements.draftContent.style.display = 'block';
        } else {
            handleDraftApiError(response.status, data);
        }
    } catch (err) {
        if (elements.draftLoading) elements.draftLoading.style.display = 'none';
        showDraftAlert('Không thể kết nối đến máy chủ. Vui lòng kiểm tra lại dịch vụ backend.', 'error');
    } finally {
        if (elements.btnPlanDraft) {
            elements.btnPlanDraft.disabled = false;
            elements.btnPlanDraft.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> Lập lịch trình nháp';
        }
    }
}

function handleDraftApiError(statusCode, data) {
    if (elements.draftLoading) elements.draftLoading.style.display = 'none';
    if (elements.draftContent) elements.draftContent.style.display = 'none';

    const message = (data && data.message) ? data.message : 'Đã xảy ra lỗi khi tạo lịch nháp.';
    const safeMessage = escapeHtml(message);

    switch (statusCode) {
        case 400:
            showDraftAlert(`Yêu cầu không hợp lệ (HTTP 400): ${safeMessage}`, 'warning');
            break;
        case 401:
            showDraftAlert(`Yêu cầu xác thực (HTTP 401): ${safeMessage}`, 'warning');
            break;
        case 403:
            showDraftAlert(`Không có quyền truy cập (HTTP 403): ${safeMessage}`, 'warning');
            break;
        case 404: {
            const warningDetails = data && Array.isArray(data.warnings) && data.warnings.length > 0
                ? `<br><small style="display:block; margin-top:4px;">${data.warnings.map(escapeHtml).join('<br>')}</small>`
                : '';
            showDraftAlert(`Không tìm thấy địa điểm phù hợp (HTTP 404): ${safeMessage}${warningDetails}`, 'warning');
            break;
        }
        case 422: {
            const warningDetails = data && Array.isArray(data.warnings) && data.warnings.length > 0
                ? `<br><small style="display:block; margin-top:4px;">${data.warnings.map(escapeHtml).join('<br>')}</small>`
                : '';
            showDraftAlert(`Lịch trình nháp không vượt qua kiểm định ràng buộc (HTTP 422): ${safeMessage}${warningDetails}`, 'error');
            break;
        }
        case 500:
            showDraftAlert('Đã xảy ra lỗi hệ thống trên máy chủ (HTTP 500). Vui lòng thử lại sau.', 'error');
            break;
        case 503:
            showDraftAlert('PostgreSQL chưa chạy hoặc bảng POI chưa sẵn sàng. Khởi động cơ sở dữ liệu rồi thử lại.', 'error');
            break;
        default:
            showDraftAlert(`Lỗi ${statusCode}: ${safeMessage}`, 'error');
            break;
    }
}

function renderDraftItinerary(data) {
    const draft = data.draft || {};
    const validation = data.validation || {};
    const summary = validation.summary || {};
    const items = draft.items || [];
    const routeLegs = validation.route_legs || data.route_legs || [];

    // 1. Meta / Summary Info
    if (elements.draftIdText) {
        elements.draftIdText.textContent = draft.draft_id || 'Chưa có mã';
    }
    if (elements.draftDateText) {
        elements.draftDateText.textContent = draft.selected_date || 'Chưa xác định';
    }
    if (elements.draftStopsCount) {
        elements.draftStopsCount.textContent = `${items.length} điểm`;
    }
    if (elements.draftDistanceText) {
        const dist = summary.total_estimated_distance_km != null 
            ? summary.total_estimated_distance_km 
            : (summary.total_distance_km != null ? summary.total_distance_km : 0);
        elements.draftDistanceText.textContent = `${dist} km (ước tính)`;
    }
    if (elements.draftDurationText) {
        if (summary.total_span_hours === null || summary.total_span_hours === undefined) {
            elements.draftDurationText.textContent = 'chưa có dữ liệu';
        } else {
            elements.draftDurationText.textContent = `${summary.total_span_hours} giờ`;
        }
    }

    // 2. Warnings
    const allWarnings = [];
    if (Array.isArray(data.warnings)) {
        allWarnings.push(...data.warnings);
    }
    if (validation && Array.isArray(validation.warnings)) {
        validation.warnings.forEach(w => {
            if (!allWarnings.includes(w)) allWarnings.push(w);
        });
    }

    if (elements.draftWarningsContainer) {
        if (allWarnings.length > 0) {
            elements.draftWarningsContainer.style.display = 'block';
            elements.draftWarningsContainer.innerHTML = `
                <h4><i class="fa-solid fa-triangle-exclamation"></i> Cảnh báo & Lưu ý kiểm định (${allWarnings.length})</h4>
                <ul>
                    ${allWarnings.map(w => `<li>${escapeHtml(w)}</li>`).join('')}
                </ul>
            `;
        } else {
            elements.draftWarningsContainer.style.display = 'none';
            elements.draftWarningsContainer.innerHTML = '';
        }
    }

    // 3. Stops and Route Legs Timeline
    if (elements.draftItemsTimeline) {
        if (items.length === 0) {
            elements.draftItemsTimeline.innerHTML = '<p style="color: var(--text-muted); text-align: center; padding: 20px;">Lịch trình không có điểm dừng nào.</p>';
            return;
        }

        let html = '';
        items.forEach((item, index) => {
            const stopIndex = item.order_index != null ? item.order_index : index + 1;
            const startTime = item.start_time || '--:--';
            const endTime = item.end_time || '--:--';
            const duration = item.estimated_duration ? `${item.estimated_duration} phút` : '';

            // Card for Stop
            html += `
                <div class="draft-stop-card">
                    <div class="draft-stop-header">
                        <div class="draft-stop-title-group">
                            <span class="draft-stop-num">Điểm ${stopIndex}</span>
                            <h3 class="draft-stop-name">${escapeHtml(item.name || 'Địa điểm không tên')}</h3>
                        </div>
                        <div class="draft-stop-time-badge">
                            <i class="fa-regular fa-clock"></i>
                            <span>${escapeHtml(startTime)} - ${escapeHtml(endTime)}</span>
                            ${duration ? `<small>(${escapeHtml(duration)})</small>` : ''}
                        </div>
                    </div>
                    <div class="draft-stop-body">
                        ${item.fclass ? `<span class="badge" style="margin-bottom: 8px;">${escapeHtml(item.fclass)}</span>` : ''}
                        ${item.address ? `<div class="draft-stop-address"><i class="fa-solid fa-location-dot"></i> <span>${escapeHtml(item.address)}</span></div>` : ''}
                        ${item.reason ? `<div class="draft-stop-reason"><i class="fa-solid fa-wand-magic-sparkles"></i> <strong>Lý do chọn:</strong> ${escapeHtml(item.reason)}</div>` : ''}
                    </div>
                </div>
            `;

            // If there is a next stop, render the route leg between stopIndex and stopIndex + 1
            if (index < items.length - 1) {
                const leg = routeLegs.find(l => {
                    const fromIdx = (typeof l.from_stop === 'object' && l.from_stop !== null) ? l.from_stop.order_index : l.from_stop;
                    const toIdx = (typeof l.to_stop === 'object' && l.to_stop !== null) ? l.to_stop.order_index : l.to_stop;
                    return (fromIdx === stopIndex && toIdx === stopIndex + 1) || (fromIdx === index + 1 && toIdx === index + 2);
                }) || routeLegs[index] || null;

                if (leg) {
                    const distKm = leg.distance_km != null ? leg.distance_km : 0;
                    const distLabel = escapeHtml(leg.distance_label || leg.distance_type || 'ước tính');
                    
                    // RÀNG BUỘC: Nếu thời gian di chuyển là null, hiển thị "chưa có dữ liệu", không tự đoán
                    let timeDisplay = '';
                    if (leg.travel_time_minutes === null || leg.travel_time_minutes === undefined) {
                        timeDisplay = '<span class="leg-pill leg-time-null"><i class="fa-regular fa-clock"></i> Thời gian: <strong>chưa có dữ liệu</strong></span>';
                    } else {
                        timeDisplay = `<span class="leg-pill"><i class="fa-regular fa-clock"></i> Thời gian: <strong>${escapeHtml(leg.travel_time_minutes)} phút</strong></span>`;
                    }

                    const warningDisplay = leg.warning ? `<span class="leg-warning"><i class="fa-solid fa-circle-exclamation"></i> ${escapeHtml(leg.warning)}</span>` : '';

                    html += `
                        <div class="draft-route-leg">
                            <span class="leg-pill"><i class="fa-solid fa-arrows-up-down"></i> Khoảng cách: <strong>${distKm} km</strong> (${distLabel})</span>
                            ${timeDisplay}
                            ${warningDisplay}
                        </div>
                    `;
                } else {
                    html += `
                        <div class="draft-route-leg">
                            <span class="leg-pill"><i class="fa-solid fa-route"></i> Khoảng cách: <strong>ước tính</strong></span>
                            <span class="leg-pill leg-time-null"><i class="fa-regular fa-clock"></i> Thời gian: <strong>chưa có dữ liệu</strong></span>
                        </div>
                    `;
                }
            }
        });

        elements.draftItemsTimeline.innerHTML = html;
    }
}

function getExistingAuthToken() {
    // Ưu tiên sessionStorage; vẫn đọc token cũ để tương thích các phiên đăng nhập trước.
    const token = sessionStorage.getItem('access_token') ||
                  sessionStorage.getItem('token') ||
                  localStorage.getItem('access_token') ||
                  localStorage.getItem('token');
    return token ? token.trim() : null;
}

function setupAuth() {
    if (elements.authOpenBtn) elements.authOpenBtn.addEventListener('click', openAuthDialog);
    if (elements.authCloseBtn) elements.authCloseBtn.addEventListener('click', closeAuthDialog);
    if (elements.authCancelBtn) elements.authCancelBtn.addEventListener('click', closeAuthDialog);
    if (elements.authLogoutBtn) elements.authLogoutBtn.addEventListener('click', logoutUser);
    if (elements.authForm) elements.authForm.addEventListener('submit', handleAuthSubmit);
    if (elements.authModal) {
        elements.authModal.addEventListener('click', (event) => {
            if (event.target === elements.authModal) closeAuthDialog();
        });
    }
    updateAuthControls();
}

function updateAuthControls() {
    const token = getExistingAuthToken();
    let user = null;
    try {
        user = JSON.parse(sessionStorage.getItem('viet_travel_user') || 'null');
    } catch (error) {
        user = null;
    }

    if (elements.authUserLabel) {
        elements.authUserLabel.textContent = token
            ? (user && (user.full_name || user.email)) || 'Đã đăng nhập'
            : 'Khách';
    }
    if (elements.authOpenBtn) elements.authOpenBtn.hidden = Boolean(token);
    if (elements.authLogoutBtn) elements.authLogoutBtn.hidden = !token;
}

function openAuthDialog() {
    if (!elements.authModal) return;
    if (elements.authError) {
        elements.authError.hidden = true;
        elements.authError.textContent = '';
    }
    elements.authModal.classList.add('active');
    if (elements.authEmail) elements.authEmail.focus();
}

function closeAuthDialog() {
    if (elements.authModal) elements.authModal.classList.remove('active');
}

function clearAuthSession() {
    ['access_token', 'token', 'viet_travel_user'].forEach((key) => {
        sessionStorage.removeItem(key);
    });
    ['access_token', 'token'].forEach((key) => localStorage.removeItem(key));
    updateAuthControls();
}

function logoutUser() {
    clearAuthSession();
    showConfirmAlert('Đã đăng xuất khỏi tài khoản.', 'info');
}

async function handleAuthSubmit(event) {
    event.preventDefault();
    const email = elements.authEmail ? elements.authEmail.value.trim() : '';
    const password = elements.authPassword ? elements.authPassword.value : '';
    if (!email || !password) return;

    if (elements.authError) {
        elements.authError.hidden = true;
        elements.authError.textContent = '';
    }
    if (elements.authSubmitBtn) {
        elements.authSubmitBtn.disabled = true;
        elements.authSubmitBtn.textContent = 'Đang đăng nhập...';
    }

    try {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.token) {
            if (elements.authError) {
                elements.authError.textContent = data.message || 'Đăng nhập không thành công.';
                elements.authError.hidden = false;
            }
            return;
        }

        // Keep the bearer token for this browser session only.
        sessionStorage.setItem('access_token', data.token);
        sessionStorage.setItem('viet_travel_user', JSON.stringify(data.user || {}));
        if (elements.authPassword) elements.authPassword.value = '';
        updateAuthControls();
        closeAuthDialog();
        if (state.currentDraft && state.currentDraft.draft) {
            showConfirmAlert('Đăng nhập thành công. Bấm “Xác nhận và lưu” để lưu lịch trình.', 'success');
        }
    } catch (error) {
        if (elements.authError) {
            elements.authError.textContent = 'Không thể kết nối máy chủ đăng nhập. Hãy thử lại.';
            elements.authError.hidden = false;
        }
    } finally {
        if (elements.authSubmitBtn) {
            elements.authSubmitBtn.disabled = false;
            elements.authSubmitBtn.textContent = 'Đăng nhập';
        }
    }
}

async function handleConfirmDraftClick() {
    hideConfirmAlert();

    if (!state.currentDraft || !state.currentDraft.draft) {
        showConfirmAlert('Không tìm thấy lịch trình nháp để xác nhận.', 'warning');
        return;
    }

    const token = getExistingAuthToken();
    if (!token) {
        showConfirmAlert('Đăng nhập để lưu lịch trình.', 'warning');
        openAuthDialog();
        return;
    }

    const draft = state.currentDraft.draft;
    const items = (draft.items || []).map(item => ({
        osm_id: item.osm_id,
        order_index: item.order_index,
        start_time: item.start_time,
        end_time: item.end_time
    }));

    // Chuẩn bị payload: TUYỆT ĐỐI không gửi user_id theo yêu cầu
    const confirmPayload = {
        draft_id: draft.draft_id,
        selected_date: draft.selected_date,
        title: `Lịch trình ngày ${draft.selected_date}`,
        notes: 'Lịch trình được tạo từ AI Planner',
        items: items
    };

    if (elements.btnConfirmDraft) {
        elements.btnConfirmDraft.disabled = true;
        elements.btnConfirmDraft.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Đang xác nhận & lưu...';
    }

    try {
        const response = await fetch('/api/itinerary/confirm', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify(confirmPayload)
        });

        const resData = await response.json().catch(() => ({}));

        if (response.status === 200 || response.status === 201) {
            const successMsg = escapeHtml(resData.message || 'Lịch trình đã được xác nhận và lưu vào cơ sở dữ liệu thành công!');
            const itineraryIdInfo = resData.itinerary_id ? ` (Mã lịch trình: #${escapeHtml(resData.itinerary_id)})` : '';
            showConfirmAlert(`${successMsg}${itineraryIdInfo}`, 'success');
            if (elements.btnConfirmDraft) {
                elements.btnConfirmDraft.disabled = true;
                elements.btnConfirmDraft.innerHTML = '<i class="fa-solid fa-check"></i> Đã xác nhận & lưu';
            }
        } else {
            handleConfirmApiError(response.status, resData);
            if (elements.btnConfirmDraft) {
                elements.btnConfirmDraft.disabled = false;
                elements.btnConfirmDraft.innerHTML = '<i class="fa-solid fa-circle-check"></i> Xác nhận & Lưu lịch trình';
            }
        }
    } catch (err) {
        showConfirmAlert('Không thể kết nối đến máy chủ khi xác nhận lịch trình. Vui lòng kiểm tra lại kết nối mạng.', 'error');
        if (elements.btnConfirmDraft) {
            elements.btnConfirmDraft.disabled = false;
            elements.btnConfirmDraft.innerHTML = '<i class="fa-solid fa-circle-check"></i> Xác nhận & Lưu lịch trình';
        }
    }
}

function handleConfirmApiError(statusCode, data) {
    const message = (data && data.message) ? data.message : 'Thao tác không thành công.';
    const safeMessage = escapeHtml(message);

    switch (statusCode) {
        case 400:
            showConfirmAlert(`Dữ liệu xác nhận không hợp lệ (HTTP 400): ${safeMessage}`, 'warning');
            break;
        case 401:
            clearAuthSession();
            showConfirmAlert('Phiên đăng nhập không hợp lệ hoặc đã hết hạn (HTTP 401). Vui lòng đăng nhập lại.', 'error');
            break;
        case 403:
            showConfirmAlert(`Tài khoản chưa được xác thực email hoặc không có quyền thực hiện (HTTP 403): ${safeMessage}`, 'error');
            break;
        case 404:
            showConfirmAlert(`Không tìm thấy bản ghi lịch trình nháp hoặc POI tương ứng (HTTP 404): ${safeMessage}`, 'error');
            break;
        case 422:
            showConfirmAlert(`Lịch trình nháp không vượt qua kiểm định ràng buộc khi lưu (HTTP 422): ${safeMessage}`, 'error');
            break;
        case 500:
            showConfirmAlert('Đã xảy ra lỗi hệ thống trên máy chủ khi lưu lịch trình (HTTP 500). Vui lòng thử lại sau.', 'error');
            break;
        default:
            showConfirmAlert(`Lỗi ${statusCode}: ${safeMessage}`, 'error');
            break;
    }
}

function handleDiscardDraftClick() {
    state.currentDraft = null;
    if (elements.draftResultSection) elements.draftResultSection.style.display = 'none';
    if (elements.draftContent) elements.draftContent.style.display = 'none';
    if (elements.draftLoading) elements.draftLoading.style.display = 'none';
    hideDraftAlert();
    hideConfirmAlert();
    if (elements.btnConfirmDraft) {
        elements.btnConfirmDraft.disabled = false;
        elements.btnConfirmDraft.innerHTML = '<i class="fa-solid fa-circle-check"></i> Xác nhận & Lưu lịch trình';
    }
}

function showDraftAlert(msg, type = 'error') {
    if (!elements.draftAlert) return;
    if (elements.draftResultSection) elements.draftResultSection.style.display = 'block';
    elements.draftAlert.className = `alert-box alert-${type}`;
    elements.draftAlert.innerHTML = msg;
    elements.draftAlert.style.display = 'block';
    elements.draftAlert.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function hideDraftAlert() {
    if (elements.draftAlert) {
        elements.draftAlert.style.display = 'none';
        elements.draftAlert.innerHTML = '';
    }
}

function showConfirmAlert(msg, type = 'error') {
    if (!elements.confirmStatusAlert) return;
    elements.confirmStatusAlert.className = `alert-box alert-${type}`;
    elements.confirmStatusAlert.innerHTML = msg;
    elements.confirmStatusAlert.style.display = 'block';
}

function hideConfirmAlert() {
    if (elements.confirmStatusAlert) {
        elements.confirmStatusAlert.style.display = 'none';
        elements.confirmStatusAlert.innerHTML = '';
    }
}

function addToItinerary(item) {
    const exists = state.itinerary.some(i => i.id === item.id);
    if (exists) {
        alert('Địa điểm này đã có trong lịch trình của bạn rồi!');
        return;
    }
    state.itinerary.push(item);
    saveItinerary();
    alert(`Đã thêm "${item.ten_dia_diem}" vào lịch trình!`);
}

function addToItineraryFromChat(idOrLoc) {
    let loc = null;
    if (typeof idOrLoc === 'object' && idOrLoc !== null) {
        loc = idOrLoc;
    } else if (state.chatPois && state.chatPois[String(idOrLoc)]) {
        loc = state.chatPois[String(idOrLoc)];
    } else {
        const strId = String(idOrLoc);
        loc = state.attractions.find(i => String(i.id) === strId) || state.itinerary.find(i => String(i.id) === strId);
    }

    if (!loc) {
        alert('Không tìm thấy thông tin địa điểm để thêm vào lịch trình.');
        return;
    }

    addToItinerary({
        id: loc.id,
        ten_dia_diem: loc.ten_dia_diem,
        loai_dia_diem: loc.loai_dia_diem,
        khu_vuc: loc.khu_vuc,
        dia_chi: loc.dia_chi,
        mo_ta: loc.mo_ta,
        latitude: loc.latitude,
        longitude: loc.longitude,
        google_maps_url: loc.google_maps_url
    });
}

function removeFromItinerary(id) {
    state.itinerary = state.itinerary.filter(i => String(i.id) !== String(id));
    saveItinerary();
    renderItinerary();
}

function saveItinerary() {
    localStorage.setItem('viet_travel_itinerary', JSON.stringify(state.itinerary));
    updateItineraryBadge();
}

function updateItineraryBadge() {
    elements.itineraryCount.textContent = state.itinerary.length;
}

function renderItinerary() {
    if (state.itinerary.length === 0) {
        elements.itineraryEmptyState.style.display = 'block';
        elements.itineraryTimeline.style.display = 'none';
        return;
    }

    elements.itineraryEmptyState.style.display = 'none';
    elements.itineraryTimeline.style.display = 'flex';

    elements.itineraryTimeline.innerHTML = state.itinerary.map((item, index) => `
        <div class="timeline-item">
            <div style="flex-grow: 1; margin-right: 16px;">
                <div style="display: flex; gap: 8px; margin-bottom: 6px;">
                    <span class="badge">Điểm ${index + 1}</span>
                    <span class="badge badge-district">${escapeHtml(item.khu_vuc || 'Việt Nam')}</span>
                </div>
                <h3 style="font-family: var(--font-heading); font-size: 17px; margin-bottom: 4px;">${escapeHtml(item.ten_dia_diem)}</h3>
                <p style="font-size: 13px; color: var(--text-secondary);">${escapeHtml(item.dia_chi || '')}</p>
            </div>
            <button class="btn-secondary" style="color: var(--danger); border-color: rgba(239, 68, 68, 0.3);" onclick="removeFromItinerary('${item.id}')">
                <i class="fa-solid fa-trash-can"></i> Xóa
            </button>
        </div>
    `).join('');
}

/* ============================================================
   6. MODAL DETAIL
   ============================================================ */
function setupModal() {
    elements.modalCloseBtn.addEventListener('click', closeModal);
    elements.modal.addEventListener('click', (e) => {
        if (e.target === elements.modal) closeModal();
    });

    elements.modalAddItineraryBtn.addEventListener('click', () => {
        if (state.activeDetailItem) {
            addToItinerary(state.activeDetailItem);
            closeModal();
        }
    });
}

function openModalWithItem(item) {
    if (!item) return;
    state.activeDetailItem = item;
    
    const modalImg = document.getElementById('modal-img');
    const modalImgBox = document.getElementById('modal-banner-box');
    if (modalImg) {
        if (item.image_url) {
            modalImg.src = item.image_url;
            if (modalImgBox) modalImgBox.style.display = 'block';
        } else {
            if (modalImgBox) modalImgBox.style.display = 'none';
        }
    }

    elements.modalTitle.textContent = item.ten_dia_diem || 'Địa điểm';
    elements.modalCategory.textContent = item.loai_dia_diem || 'Địa điểm';
    elements.modalDistrict.textContent = item.khu_vuc || 'Việt Nam';
    elements.modalDesc.textContent = item.mo_ta || 'Chưa có thông tin mô tả chi tiết.';
    elements.modalAddress.textContent = item.dia_chi || 'Đang cập nhật';
    elements.modalHours.textContent = item.thoi_gian_hoat_dong || 'Chưa có thông tin';
    elements.modalPrice.textContent = item.gia_ve || 'Chưa có thông tin';
    elements.modalRating.textContent = item.danh_gia ? `${item.danh_gia} / 5.0` : 'Chưa có đánh giá';
    
    const mapsLink = document.getElementById('modal-maps-link');
    if (mapsLink) {
        let mapsUrl = item.google_maps_url;
        if (!mapsUrl && item.latitude && item.longitude) {
            mapsUrl = `https://www.google.com/maps?q=${item.latitude},${item.longitude}`;
        }
        if (!mapsUrl) {
            mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent((item.ten_dia_diem || '') + ' ' + (item.khu_vuc || 'Việt Nam'))}`;
        }
        mapsLink.href = mapsUrl;
    }

    elements.modal.classList.add('active');
}

function openDetailById(id) {
    const strId = String(id);
    if (state.chatPois && state.chatPois[strId]) {
        openModalWithItem(state.chatPois[strId]);
        return;
    }
    const item = state.attractions.find(i => String(i.id) === strId);
    if (item) {
        openModalWithItem(item);
        return;
    }
    const itinItem = state.itinerary.find(i => String(i.id) === strId);
    if (itinItem) {
        openModalWithItem(itinItem);
        return;
    }
    fetch(`/api/travel-chatbot/metadata?page=1&limit=100`)
        .then(res => res.json())
        .then(data => {
            const found = (data.metadata || []).find(i => String(i.id) === strId);
            if (found) {
                openModalWithItem(found);
            } else {
                alert('Không tìm thấy thông tin chi tiết của địa điểm này.');
            }
        })
        .catch(err => {
            console.error('Error opening detail:', err);
            alert('Không thể mở thông tin địa điểm.');
        });
}

function closeModal() {
    elements.modal.classList.remove('active');
    state.activeDetailItem = null;
}

/* ============================================================
   HELPERS
   ============================================================ */
function getTimeNow() {
    const d = new Date();
    return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function formatMarkdown(text) {
    if (!text) return '';

    // 1. Chuẩn hóa xuống dòng
    let s = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

    // 2. Loại bỏ triệt để các dòng chỉ chứa dấu thăng thừa hoặc khoảng trống
    s = s.replace(/^[ \t]*#+[ \t]*$/gm, '');

    // 3. Escape HTML trước để đảm bảo an toàn XSS
    let html = escapeHtml(s);

    // 3.4. Parse Markdown images ![Alt](URL) thành khung ảnh trực quan như cẩm nang du lịch
    html = html.replace(/!\[([^\]]*)\]\((https?:\/\/[^\s\)\&]+(?:\&amp;[^\s\)]*)*)\)/g, `
        <div class="chat-inline-img-box">
            <img src="$2" alt="$1" class="chat-inline-img" loading="lazy" onerror="this.parentElement.style.display='none'">
            <span class="chat-inline-caption"><i class="fa-solid fa-camera"></i> $1</span>
        </div>
    `);

    // 3.5. Parse Markdown links [Title](URL) thành thẻ <a> có thể bấm mở trực tiếp
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s\)\&]+(?:\&amp;[^\s\)]*)*)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" class="chat-inline-link"><i class="fa-solid fa-arrow-up-right-from-square"></i> $1</a>');
    html = html.replace(/\[\+?\s*Thêm vào lịch(?:\s*trình)?\]/gi, '');
    html = html.replace(/\|\s*(?=\n|<br>|$)/gm, '');

    // 4. Đường phân cách ngang (---, ***, ___)
    html = html.replace(/^[ \t]*[-*_]{3,}[ \t]*$/gm, '<hr class="chat-divider">');

    // 5. Tiêu đề Markdown H4, H3, H2, H1 trực tiếp
    html = html.replace(/^[ \t]*####[ \t]+(.*)$/gm, '<h4 class="chat-heading-4">$1</h4>');
    html = html.replace(/^[ \t]*###[ \t]+(.*)$/gm, '<h4 class="chat-heading-4">$1</h4>');
    html = html.replace(/^[ \t]*##[ \t]+(.*)$/gm, '<h3 class="chat-heading-3">$1</h3>');
    html = html.replace(/^[ \t]*#[ \t]+(.*)$/gm, '<h3 class="chat-heading-3">$1</h3>');

    // 6. In đậm & In nghiêng chuẩn
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');

    // 7. Dọn dẹp triệt để các dấu sao thừa rớt lại (ví dụ "Địa chỉ*:" -> "Địa chỉ:")
    html = html.replace(/([a-zA-Z0-9À-ỹ\s])\*(:|\b)/g, '$1$2');
    html = html.replace(/(:\s*)\*/g, '$1');
    html = html.replace(/\*([:;,.-])/g, '$1');

    // 8. Danh sách dấu chấm (bullet points)
    html = html.replace(/^[ \t]*[-*+][ \t]+/gm, '• ');

    // 9. Đổi ký tự xuống dòng thành <br> và dọn dẹp khoảng trống
    html = html.replace(/\n/g, '<br>');
    html = html.replace(/(<br>\s*){3,}/g, '<br><br>');
    html = html.replace(/(<\/h[34]>)<br>/g, '$1');
    html = html.replace(/(<hr class="chat-divider">)<br>/g, '$1');

    return html;
}

function scrollToBottom() {
    elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
}

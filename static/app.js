// scene.org Music Discovery - Frontend

const $ = (sel) => document.querySelector(sel);
const audio = new Audio();
let state = {
    track: null,
    collection: null,
    categoryName: '',
    scope: 'track',
    isPlaying: false,
    isLoading: false,
    browseStack: [],  // navigation stack for browse panel
};

// ── API Helpers ──

async function api(method, path, body) {
    const opts = { method, headers: {} };
    if (body) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    const resp = await fetch(path, opts);
    if (!resp.ok) throw new Error(`API ${resp.status}: ${resp.statusText}`);
    return resp.json();
}

// ── Player Logic ──

async function loadCurrent() {
    try {
        const data = await api('GET', '/api/player/current');
        applyPlayerState(data);
    } catch (e) {
        console.error('Failed to load current track:', e);
        // Might not have any tracks yet, try again in a few seconds
        setTimeout(loadCurrent, 3000);
    }
}

async function nextTrack() {
    if (state.isLoading) return;
    setLoading(true);
    try {
        const data = await api('POST', `/api/player/next?scope=${state.scope}`);
        applyPlayerState(data);
        playTrack();
    } catch (e) {
        console.error('Next failed:', e);
        showToast('Failed to load next track');
        setLoading(false);
    }
}

async function prevTrack() {
    if (state.isLoading) return;
    setLoading(true);
    try {
        const data = await api('POST', '/api/player/prev');
        applyPlayerState(data);
        playTrack();
    } catch (e) {
        showToast('No previous track');
        setLoading(false);
    }
}

function applyPlayerState(data) {
    if (!data.track) {
        $('#trackTitle').textContent = 'No tracks available';
        $('#collectionName').textContent = 'Waiting for crawl...';
        $('#breadcrumb').textContent = '';
        return;
    }
    state.track = data.track;
    state.collection = data.collection;
    state.categoryName = data.category_name;

    // Update UI
    $('#trackTitle').textContent = data.track.title;
    $('#collectionName').textContent = data.collection ? data.collection.name : '--';
    $('#breadcrumb').textContent = data.category_name +
        (data.collection ? ' > ' + data.collection.name : '');

    // Format badge in title area (append to breadcrumb)
    const fmt = data.track.format.toUpperCase();
    $('#breadcrumb').innerHTML += ` <span class="format-badge">${fmt}</span>`;

    // Update heart
    updateHeart(data.track.upvoted);

    // Update prev button state
    $('#prevBtn').style.opacity = data.has_prev ? '1' : '0.3';

    // Load art
    loadArt(data.collection);
}

function loadArt(collection) {
    const img = $('#artImg');
    const placeholder = $('#artPlaceholder');

    if (collection && collection.art_url) {
        img.src = `/api/art/${collection.id}`;
        img.style.display = 'block';
        placeholder.style.display = 'none';
        img.onerror = () => {
            img.style.display = 'none';
            placeholder.style.display = 'block';
            setPlaceholderColor(collection.name);
        };
    } else {
        img.style.display = 'none';
        placeholder.style.display = 'block';
        setPlaceholderColor(collection ? collection.name : 'music');
    }
}

function setPlaceholderColor(name) {
    // Generate consistent color from name
    let hash = 0;
    for (let i = 0; i < name.length; i++) {
        hash = name.charCodeAt(i) + ((hash << 5) - hash);
    }
    const hue = Math.abs(hash) % 360;
    $('#artBox').style.background =
        `linear-gradient(135deg, hsl(${hue}, 40%, 15%), hsl(${(hue + 60) % 360}, 30%, 10%))`;
}

function playTrack() {
    if (!state.track) return;
    setLoading(true);

    audio.src = `/api/player/stream/${state.track.id}`;
    audio.load();

    const onCanPlay = () => {
        setLoading(false);
        audio.play().catch(e => console.log('Autoplay blocked:', e));
        state.isPlaying = true;
        updatePlayBtn();
        audio.removeEventListener('canplay', onCanPlay);
    };
    audio.addEventListener('canplay', onCanPlay);
}

function togglePlay() {
    if (!state.track) {
        nextTrack();
        return;
    }
    if (audio.paused) {
        if (!audio.src || audio.src === window.location.href) {
            playTrack();
        } else {
            audio.play();
        }
        state.isPlaying = true;
    } else {
        audio.pause();
        state.isPlaying = false;
    }
    updatePlayBtn();
}

function updatePlayBtn() {
    $('#playBtn').innerHTML = state.isPlaying ? '&#9646;&#9646;' : '&#9654;';
}

function setLoading(loading) {
    state.isLoading = loading;
    $('#loadingSpinner').style.display = loading ? 'flex' : 'none';
}

// ── Seek Bar ──

function formatTime(s) {
    if (!s || !isFinite(s)) return '0:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
}

audio.addEventListener('timeupdate', () => {
    if (audio.duration) {
        const pct = (audio.currentTime / audio.duration) * 100;
        $('#seekBar').value = pct;
        $('#currentTime').textContent = formatTime(audio.currentTime);
        $('#duration').textContent = formatTime(audio.duration);
    }
});

audio.addEventListener('loadedmetadata', () => {
    $('#duration').textContent = formatTime(audio.duration);
});

$('#seekBar').addEventListener('input', (e) => {
    if (audio.duration) {
        audio.currentTime = (e.target.value / 100) * audio.duration;
    }
});

// ── Auto-advance + error handling ──

audio.addEventListener('ended', () => {
    state.isPlaying = false;
    updatePlayBtn();
    nextTrack();
});

audio.addEventListener('error', (e) => {
    console.error('Audio error:', e);
    setLoading(false);
    showToast('Playback error, skipping...');
    setTimeout(nextTrack, 1000);
});

// ── Upvote ──

async function toggleUpvote() {
    if (!state.track) return;
    const id = state.track.id;
    try {
        if (state.track.upvoted) {
            await api('DELETE', `/api/upvote/${id}`);
            state.track.upvoted = false;
            showToast('Removed from library');
        } else {
            await api('POST', `/api/upvote/${id}`);
            state.track.upvoted = true;
            showToast('Saved to library');
        }
        updateHeart(state.track.upvoted);
    } catch (e) {
        showToast('Upvote failed');
    }
}

function updateHeart(active) {
    const btn = $('#heartBtn');
    btn.innerHTML = active ? '&#9829;' : '&#9825;';
    btn.classList.toggle('active', active);
}

// ── Scope selector ──

document.querySelectorAll('.scope-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.scope-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.scope = btn.dataset.scope;
    });
});

// ── Browse Panel ──

function openBrowse() {
    $('#browseOverlay').classList.add('open');
    $('#browsePanel').classList.add('open');
    state.browseStack = [];
    loadCategories();
}

function closeBrowse() {
    $('#browseOverlay').classList.remove('open');
    $('#browsePanel').classList.remove('open');
}

function browseBack() {
    state.browseStack.pop();
    if (state.browseStack.length === 0) {
        loadCategories();
    } else {
        const prev = state.browseStack[state.browseStack.length - 1];
        if (prev.type === 'category') {
            loadCollections(prev.name);
        } else if (prev.type === 'collection') {
            loadCollectionDetail(prev.id, prev.name);
        }
    }
}

async function loadCategories() {
    $('#browseTitle').textContent = 'Browse';
    $('#browseBack').style.display = 'none';
    $('#browseSearchInput').value = '';
    $('#browseList').innerHTML = '<div class="browse-item"><span class="icon">...</span><span class="info"><span class="name">Loading...</span></span></div>';

    try {
        const cats = await api('GET', '/api/categories');
        const list = $('#browseList');
        list.innerHTML = '';
        cats.forEach(cat => {
            const item = document.createElement('div');
            item.className = 'browse-item';
            item.innerHTML = `
                <span class="icon">&#128193;</span>
                <span class="info">
                    <span class="name">${esc(cat.name)}</span>
                    <span class="meta">${cat.collection_count} collections &middot; ${cat.track_count} tracks</span>
                </span>
            `;
            item.addEventListener('click', () => {
                state.browseStack.push({ type: 'category', name: cat.name });
                loadCollections(cat.name);
            });
            list.appendChild(item);
        });
    } catch (e) {
        $('#browseList').innerHTML = '<div class="browse-item"><span class="icon">!</span><span class="info"><span class="name">Failed to load</span></span></div>';
    }
}

async function loadCollections(category, query) {
    $('#browseTitle').textContent = category;
    $('#browseBack').style.display = 'block';
    const list = $('#browseList');
    list.innerHTML = '<div class="browse-item"><span class="icon">...</span><span class="info"><span class="name">Loading...</span></span></div>';

    try {
        let url = `/api/collections?category=${encodeURIComponent(category)}&limit=100`;
        if (query) url += `&q=${encodeURIComponent(query)}`;
        const collections = await api('GET', url);
        list.innerHTML = '';
        if (collections.length === 0) {
            list.innerHTML = '<div class="browse-item"><span class="info"><span class="name" style="color:var(--fg2)">No collections found</span></span></div>';
            return;
        }
        collections.forEach(col => {
            const item = document.createElement('div');
            item.className = 'browse-item';
            item.innerHTML = `
                <span class="icon">&#9835;</span>
                <span class="info">
                    <span class="name">${esc(col.name)}</span>
                    <span class="meta">${col.track_count} tracks</span>
                </span>
            `;
            item.addEventListener('click', () => {
                state.browseStack.push({ type: 'collection', id: col.id, name: col.name });
                loadCollectionDetail(col.id, col.name);
            });
            list.appendChild(item);
        });
    } catch (e) {
        list.innerHTML = '<div class="browse-item"><span class="icon">!</span><span class="info"><span class="name">Failed to load</span></span></div>';
    }
}

async function loadCollectionDetail(id, name) {
    $('#browseTitle').textContent = name;
    $('#browseBack').style.display = 'block';
    const list = $('#browseList');
    list.innerHTML = '<div class="browse-item"><span class="icon">...</span><span class="info"><span class="name">Loading...</span></span></div>';

    try {
        const detail = await api('GET', `/api/collections/${id}`);
        list.innerHTML = '';
        detail.tracks.forEach(track => {
            const item = document.createElement('div');
            item.className = 'browse-item';
            const heart = track.upvoted ? '&#9829;' : '';
            item.innerHTML = `
                <span class="icon">&#9654;</span>
                <span class="info">
                    <span class="name">${esc(track.title)} <span class="format-badge">${track.format}</span> ${heart}</span>
                    <span class="meta">${track.filename}</span>
                </span>
            `;
            item.addEventListener('click', () => {
                playSpecificTrack(track);
                closeBrowse();
            });
            list.appendChild(item);
        });
    } catch (e) {
        list.innerHTML = '<div class="browse-item"><span class="icon">!</span><span class="info"><span class="name">Failed to load</span></span></div>';
    }
}

function playSpecificTrack(track) {
    state.track = track;
    // We need to update the UI with partial info
    $('#trackTitle').textContent = track.title;
    const fmt = track.format.toUpperCase();
    $('#breadcrumb').innerHTML = `<span class="format-badge">${fmt}</span>`;
    updateHeart(track.upvoted);
    setLoading(true);
    audio.src = `/api/player/stream/${track.id}`;
    audio.load();

    const onCanPlay = () => {
        setLoading(false);
        audio.play().catch(() => {});
        state.isPlaying = true;
        updatePlayBtn();
        audio.removeEventListener('canplay', onCanPlay);
    };
    audio.addEventListener('canplay', onCanPlay);
}

// Search with debounce
let searchTimeout;
$('#browseSearchInput').addEventListener('input', (e) => {
    clearTimeout(searchTimeout);
    const q = e.target.value.trim();
    searchTimeout = setTimeout(() => {
        const current = state.browseStack[state.browseStack.length - 1];
        if (current && current.type === 'category') {
            loadCollections(current.name, q);
        }
    }, 300);
});

// ── Toast ──

function showToast(msg) {
    const t = $('#toast');
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2500);
}

// ── Status polling ──

async function pollStatus() {
    try {
        const s = await api('GET', '/api/status');
        const dot = $('#statusDot');
        if (s.crawl_status === 'running') {
            dot.className = 'status-dot crawling';
            dot.title = `Crawling... ${s.total_tracks} tracks found`;
        } else if (s.crawl_status === 'complete') {
            dot.className = 'status-dot ready';
            dot.title = `${s.total_tracks} tracks`;
        } else {
            dot.className = 'status-dot';
            dot.title = s.crawl_status;
        }
    } catch (e) {
        // ignore
    }
}

// ── Keyboard shortcuts ──

document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT') return;
    switch (e.code) {
        case 'Space':
            e.preventDefault();
            togglePlay();
            break;
        case 'ArrowRight':
            e.preventDefault();
            nextTrack();
            break;
        case 'ArrowLeft':
            e.preventDefault();
            prevTrack();
            break;
        case 'KeyU':
            toggleUpvote();
            break;
    }
});

// ── Escape HTML ──

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}

// ── Event Bindings ──

$('#playBtn').addEventListener('click', togglePlay);
$('#nextBtn').addEventListener('click', nextTrack);
$('#prevBtn').addEventListener('click', prevTrack);
$('#heartBtn').addEventListener('click', toggleUpvote);
$('#menuBtn').addEventListener('click', openBrowse);
$('#browseOverlay').addEventListener('click', closeBrowse);
$('#browseClose').addEventListener('click', closeBrowse);
$('#browseBack').addEventListener('click', browseBack);

// ── Init ──

loadCurrent();
pollStatus();
setInterval(pollStatus, 5000);

// Try to load a track after crawl has some results
setTimeout(() => {
    if (!state.track) loadCurrent();
}, 5000);
setTimeout(() => {
    if (!state.track) loadCurrent();
}, 15000);

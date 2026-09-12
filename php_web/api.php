<?php
/**
 * Feed API for shared hosting.
 *
 * Replaces api.py + store.py. No database setup, no long-running process —
 * just PHP, which your host already runs. Records live in data/feed.json.
 *
 * JSON file rather than MySQL on purpose: at a few thousand tender records
 * this is fast, there is nothing to provision, and moving to MySQL later is
 * a change to two functions. Do NOT keep this once you add user accounts and
 * subscriptions — that is the point to move to a real table.
 *
 * The collector (Python, running anywhere else) POSTs new records to
 * ?action=ingest with the shared secret. Nothing else writes.
 */

declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: no-store');

// CHANGE THIS before the collector goes live. Anyone with this string can
// write to your feed.
const INGEST_SECRET = 'change-me-to-a-long-random-string';

const DATA = __DIR__ . '/data/feed.json';
const REVIEW_FLOOR = 0.7;

function load(): array {
    if (!is_readable(DATA)) return [];
    $raw = file_get_contents(DATA);
    $rows = json_decode($raw, true);
    return is_array($rows) ? $rows : [];
}

function save(array $rows): bool {
    if (!is_dir(dirname(DATA))) mkdir(dirname(DATA), 0755, true);
    // Write to a temp file then rename. rename() is atomic on the same
    // filesystem, so a reader mid-write never sees a truncated file.
    $tmp = DATA . '.tmp';
    $ok = file_put_contents(
        $tmp, json_encode($rows, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT),
        LOCK_EX
    );
    return $ok !== false && rename($tmp, DATA);
}

function out($payload, int $code = 200): never {
    http_response_code($code);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE);
    exit;
}

$action = $_GET['action'] ?? 'feed';

/* ------------------------------------------------------------------ */

if ($action === 'health') {
    $rows = load();
    out([
        'ok' => true,
        'records' => count($rows),
        'php' => PHP_VERSION,
        'writable' => is_writable(dirname(DATA)),
    ]);
}

if ($action === 'ingest') {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') out(['error' => 'POST only'], 405);

    $given = $_SERVER['HTTP_X_INGEST_SECRET'] ?? '';
    if (!hash_equals(INGEST_SECRET, $given)) out(['error' => 'bad secret'], 403);

    $body = json_decode(file_get_contents('php://input'), true);
    if (!is_array($body['items'] ?? null)) out(['error' => 'need items[]'], 400);

    $rows = load();
    $byId = [];
    foreach ($rows as $r) $byId[$r['tender_id']] = $r;

    // Status must never slide backwards. NIC re-lists amended tenders in the
    // live table after they are awarded; without this guard the feed would
    // tell a user a confirmed award had reopened for bidding.
    $order = ['announced'=>0,'tendered'=>1,'bids_opened'=>2,
              'awarded'=>3,'in_progress'=>4,'completed'=>5,'cancelled'=>6];

    $added = 0; $updated = 0;
    foreach ($body['items'] as $it) {
        $id = $it['tender_id'] ?? null;
        if (!$id) continue;
        if (isset($byId[$id])) {
            $old = $byId[$id]['status'] ?? 'tendered';
            $new = $it['status'] ?? 'tendered';
            if (($order[$old] ?? 0) > ($order[$new] ?? 0)) $it['status'] = $old;
            $it['first_seen'] = $byId[$id]['first_seen'] ?? ($it['first_seen'] ?? null);
            $updated++;
        } else {
            $added++;
        }
        $byId[$id] = $it;
    }

    if (!save(array_values($byId))) out(['error' => 'write failed'], 500);
    out(['ok' => true, 'added' => $added, 'updated' => $updated,
         'total' => count($byId)]);
}

/* ----------------------------- feed ------------------------------- */

$filter   = $_GET['filter']   ?? 'all';
$district = $_GET['district'] ?? null;
$limit    = min((int)($_GET['limit'] ?? 60), 200);

$rows = array_filter(load(), function (array $r) use ($filter, $district): bool {
    // Anything the extractor was unsure about never reaches a user.
    if ((float)($r['confidence'] ?? 0) < REVIEW_FLOOR) return false;
    if (!in_array($r['status'] ?? '', ['awarded', 'bids_opened'], true)) return false;

    $wins = $r['winner_value'] ?? null;
    $est  = $r['estimated_cost'] ?? null;

    switch ($filter) {
        case 'nh':
            if (stripos((string)($r['road_name'] ?? ''), 'NH') !== 0) return false;
            break;
        case 'bridge':
            if (!in_array($r['work_type'] ?? '', 
                ['bridge','rob','rub','flyover','culvert'], true)) return false;
            break;
        case 'big':
            if ((int)($wins ?? $est ?? 0) < 100000000) return false;
            break;
        case 'awarded':
            if (($r['status'] ?? '') !== 'awarded') return false;
            break;
    }

    if ($district) {
        $ds = $r['districts'] ?? [];
        if (!is_array($ds) || !in_array($district, $ds, true)) return false;
    }
    return true;
});

usort($rows, fn($a, $b) =>
    strcmp((string)($b['awarded_on'] ?? $b['last_seen'] ?? ''),
           (string)($a['awarded_on'] ?? $a['last_seen'] ?? '')));

$rows = array_slice(array_values($rows), 0, $limit);
out(['count' => count($rows), 'items' => $rows]);

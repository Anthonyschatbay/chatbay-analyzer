<?php
/**
 * Plugin Name: Chatbay eBay Media Handler
 * Description: Routes eBay-related uploads to /ebay-media/ (root), strips EXIF on JPGs, ensures public CORS access, and prevents WP/cache interference.
 * Author: Chatbay / Counter Culture Co
 * Version: 1.1.0
 */

/*
 |--------------------------------------------------------------------------
 | ROUTE eBay Analyzer uploads to /ebay-media (root)
 |--------------------------------------------------------------------------
 |
 | We only rewrite upload_dir when the request clearly comes from your analyzer
 | or your custom REST route. This avoids breaking normal media uploads.
 |
 */
add_filter('upload_dir', function ($dirs) {
    $is_ebay_api = false;

    // Detect analyzer / gallery calls (tweak if your source param differs)
    if (isset($_REQUEST['source']) && $_REQUEST['source'] === 'ebay') {
        $is_ebay_api = true;
    }
    if (strpos($_SERVER['REQUEST_URI'] ?? '', '/chatbay/v1/gallery') !== false) {
        $is_ebay_api = true;
    }

    if ($is_ebay_api) {
        $siteurl = rtrim(get_site_url(), '/');
        $subdir  = '/ebay-media';

        $dirs['path'] = ABSPATH . ltrim($subdir, '/');
        $dirs['basedir'] = $dirs['path'];
        $dirs['url']  = $siteurl . $subdir;
        $dirs['baseurl'] = $dirs['url'];
        $dirs['subdir'] = '';
    }

    return $dirs;
});


/*
 |--------------------------------------------------------------------------
 | STRIP EXIF from JPEG uploads (privacy + compatibility)
 |--------------------------------------------------------------------------
 */
add_filter('wp_handle_upload', function ($fileinfo) {
    $mime = $fileinfo['type'] ?? '';
    if ($mime === 'image/jpeg' && function_exists('imagecreatefromjpeg')) {
        $file = $fileinfo['file'] ?? '';
        if ($file && is_file($file)) {
            $img = @imagecreatefromjpeg($file);
            if ($img) {
                // Re-encode to remove EXIF
                imagejpeg($img, $file, 92);
                imagedestroy($img);
            }
        }
    }
    return $fileinfo;
});


/*
 |--------------------------------------------------------------------------
 | Ensure /ebay-media exists and is writable
 |--------------------------------------------------------------------------
 */
register_activation_hook(__FILE__, function () {
    $dir = ABSPATH . 'ebay-media';
    if (!file_exists($dir)) {
        wp_mkdir_p($dir);
        @chmod($dir, 0755);
    }
});


/*
 |--------------------------------------------------------------------------
 | Open CORS + long cache for /ebay-media responses
 | (no output here—just headers when /ebay-media is requested)
 |--------------------------------------------------------------------------
 */
add_action('init', function () {
    $req = $_SERVER['REQUEST_URI'] ?? '';
    if (strpos($req, '/ebay-media/') !== false) {
        // CORS for crawlers (eBay, OpenAI, etc.)
        header('Access-Control-Allow-Origin: *');
        header('Access-Control-Allow-Methods: GET, OPTIONS');
        header('Access-Control-Allow-Headers: Range');
        header('Cache-Control: public, max-age=31536000, immutable');

        // avoid stray headers
        header_remove('Set-Cookie');
        header_remove('Link');
    }
});

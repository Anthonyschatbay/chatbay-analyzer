<?php
/*
Plugin Name: Chatbay eBay Media LiteSpeed Exclude
Description: Forces LiteSpeed Cache to ignore /ebay-media/ for image optimization, lazy load, and cache.
Author: Counter Culture Co
Version: 1.0
*/

add_action('init', function() {
    // Safety: ensure LiteSpeed functions exist before running
    if (!defined('LITESPEED_ACTIVE') && !defined('LSCWP_DIR')) return;

    // Define target URI pattern
    $exclude_path = '/ebay-media/';

    // Force exclusions via LiteSpeed options filters
    add_filter('litespeed_optimize_lazyload_uri_excludes', function($excludes) use ($exclude_path) {
        if (!in_array($exclude_path, $excludes)) $excludes[] = $exclude_path;
        return $excludes;
    });

    add_filter('litespeed_optimize_lazyload_img_uri_excludes', function($excludes) use ($exclude_path) {
        if (!in_array($exclude_path, $excludes)) $excludes[] = $exclude_path;
        return $excludes;
    });

    add_filter('litespeed_optimize_lazyload_iframe_uri_excludes', function($excludes) use ($exclude_path) {
        if (!in_array($exclude_path, $excludes)) $excludes[] = $exclude_path;
        return $excludes;
    });

    // Exclude from optimization entirely
    add_filter('litespeed_optimize_img_exclude', function($excludes) use ($exclude_path) {
        if (!in_array($exclude_path, $excludes)) $excludes[] = $exclude_path;
        return $excludes;
    });

    // Exclude from caching (page-level)
    add_filter('litespeed_cache_drop_uri', function($excludes) use ($exclude_path) {
        if (!in_array($exclude_path, $excludes)) $excludes[] = $exclude_path;
        return $excludes;
    });

    // Add friendly header for external fetchers (optional)
    add_action('send_headers', function() {
        if (strpos($_SERVER['REQUEST_URI'], '/ebay-media/') !== false) {
            header('Access-Control-Allow-Origin: *');
            header('Access-Control-Allow-Methods: GET, OPTIONS');
            header('Access-Control-Allow-Headers: Range');
            header('Cache-Control: public, max-age=31536000, immutable');
        }
    });
});

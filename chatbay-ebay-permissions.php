<?php
/*
Plugin Name: Chatbay eBay Permissions Fixer
Description: Ensures correct file and folder permissions inside /ebay-media/ for all eBay uploads.
Author: Chatbay / Counter Culture Co
Version: 1.0
*/

/**
 * Auto-fix permissions after each file upload
 */
add_action('add_attachment', function($post_id) {
    $file = get_attached_file($post_id);

    // Only target uploads within /ebay-media/
    if (strpos($file, '/ebay-media/') !== false && file_exists($file)) {
        $dir = dirname($file);

        // Fix file permissions (644)
        @chmod($file, 0644);

        // Fix parent directory permissions (755)
        if (is_dir($dir)) {
            @chmod($dir, 0755);
        }

        // Safety check: recursively repair if needed
        $base = WP_CONTENT_DIR . '/ebay-media';
        if (is_dir($base)) {
            $it = new RecursiveIteratorIterator(
                new RecursiveDirectoryIterator($base, RecursiveDirectoryIterator::SKIP_DOTS),
                RecursiveIteratorIterator::SELF_FIRST
            );
            foreach ($it as $item) {
                @chmod($item->getPathname(), $item->isDir() ? 0755 : 0644);
            }
        }
    }
});

/**
 * On activation: make sure /ebay-media/ exists and is open
 */
register_activation_hook(__FILE__, function() {
    $ebay_dir = WP_CONTENT_DIR . '/ebay-media';
    if (!file_exists($ebay_dir)) {
        wp_mkdir_p($ebay_dir);
    }
    @chmod($ebay_dir, 0755);
});

/**
 * On every admin_init: background re-check permissions (failsafe)
 */
add_action('admin_init', function() {
    $base = WP_CONTENT_DIR . '/ebay-media';
    if (is_dir($base)) {
        $iterator = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator($base, RecursiveDirectoryIterator::SKIP_DOTS),
            RecursiveIteratorIterator::SELF_FIRST
        );
        foreach ($iterator as $item) {
            $perm = substr(sprintf('%o', fileperms($item)), -3);
            $target = $item->isDir() ? '755' : '644';
            if ($perm !== $target) {
                @chmod($item->getPathname(), $item->isDir() ? 0755 : 0644);
            }
        }
    }
});

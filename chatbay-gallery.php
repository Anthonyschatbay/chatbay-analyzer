<?php
/**
 * Plugin Name: Chatbay eBay Gallery (filesystem)
 * Description: Exposes /wp-json/chatbay/v1/gallery by scanning /ebay-media and grouping URLs by 4.
 * Author: Chatbay
 * Version: 1.0.0
 */

add_action('rest_api_init', function () {
    register_rest_route('chatbay/v1', '/gallery', [
        'methods'  => 'GET',
        'callback' => function () {
            $base_path = ABSPATH . 'ebay-media/';
            $base_url  = home_url('/ebay-media/');

            if (!is_dir($base_path)) {
                return [
                    'error'  => 'Directory not found',
                    'path'   => $base_path,
                    'groups' => [],
                ];
            }

            $files = glob($base_path . '*.{jpg,jpeg,png,JPG,JPEG,PNG}', GLOB_BRACE);
            sort($files, SORT_NATURAL | SORT_FLAG_CASE);

            $groups = [];
            $batch  = [];

            foreach ($files as $i => $file) {
                $filename = basename($file);
                $url      = trailingslashit($base_url) . $filename;
                $batch[]  = $url;

                if ( (($i + 1) % 4) === 0 ) {
                    $groups[] = ['photo_urls' => implode(',', $batch)];
                    $batch    = [];
                }
            }

            if (!empty($batch)) {
                $groups[] = ['photo_urls' => implode(',', $batch)];
            }

            return [
                'total_images' => is_array($files) ? count($files) : 0,
                'total_groups' => count($groups),
                'groups'       => $groups,
            ];
        },
        'permission_callback' => '__return_true',
    ]);
});

<?php
add_filter('upload_dir', function ($dirs) {
    // Force WP uploads to stay in wp-content/uploads
    $dirs['subdir']  = ''; 
    $dirs['path']    = WP_CONTENT_DIR . '/uploads';
    $dirs['url']     = content_url('/uploads');
    return $dirs;
});

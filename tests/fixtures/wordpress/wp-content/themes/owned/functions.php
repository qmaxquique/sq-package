<?php
declare(strict_types=1);

function owned_theme_title(string $title): string
{
    return htmlspecialchars($title, ENT_QUOTES, 'UTF-8');
}

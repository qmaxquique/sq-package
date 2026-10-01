<?php
declare(strict_types=1);

namespace SonarFixture;

final class Calculator
{
    public function add(int $left, int $right): int
    {
        return $left + $right;
    }

    public function subtract(int $left, int $right): int
    {
        return $left - $right;
    }
}

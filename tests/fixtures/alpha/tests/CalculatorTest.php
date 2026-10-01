<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;
use SonarFixture\Calculator;

final class CalculatorTest extends TestCase
{
    public function testAdd(): void
    {
        self::assertSame(3, (new Calculator())->add(1, 2));
    }
}

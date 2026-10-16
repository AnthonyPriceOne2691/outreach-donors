/**
 * Длинный текст в несколько строк и «показать целиком» — описание сегмента на «Сборе базы» и
 * описание гипотезы в таблице «Гипотезы» (одно правило на оба места).
 *
 * Кнопка — только когда текст правда не помещается. Это мерит браузер (`scrollHeight` против
 * `clientHeight` обрезанного текста) и перемеряет при смене ширины: число букв этого не скажет —
 * на 1440 и на 390 строк разное число, а узкое окно обрезает и короткий текст.
 */
import { Anchor, Button, Stack, Text } from '@mantine/core';
import type { MantineSize } from '@mantine/core';
import { useId, useLayoutEffect, useRef, useState } from 'react';

export function ClampText({
  text,
  lines,
  className,
  size,
  dimmed = false,
  toggle,
}: {
  text: string;
  /** Строк в свёрнутом виде. */
  lines: number;
  /** Имя класса текста — по нему тесты подставляют замер браузера. */
  className: string;
  size: MantineSize;
  dimmed?: boolean;
  /** Раскрытие: кнопкой (описание сегмента над формой) или ссылкой чернилами, как числа строки
   *  таблицы гипотез. */
  toggle: 'button' | 'link';
}) {
  const textId = useId();
  const ref = useRef<HTMLParagraphElement>(null);
  const [whole, setWhole] = useState(false);
  const [long, setLong] = useState(false);
  useLayoutEffect(() => {
    const node = ref.current;
    if (node === null || whole) return undefined;
    const measure = () => setLong(node.scrollHeight > node.clientHeight + 1);
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const watcher = new ResizeObserver(measure);
    watcher.observe(node);
    return () => watcher.disconnect();
  }, [text, whole]);
  const label = whole ? 'свернуть' : 'показать целиком';
  const control = {
    'aria-expanded': whole,
    'aria-controls': textId,
    onClick: () => setWhole(!whole),
  };
  return (
    <Stack gap={2} align="flex-start">
      <Text
        ref={ref}
        id={textId}
        size={size}
        className={className}
        {...(dimmed ? { c: 'dimmed' } : {})}
        {...(whole ? {} : { lineClamp: lines })}
      >
        {text}
      </Text>
      {(long || whole) &&
        (toggle === 'button' ? (
          <Button variant="subtle" size="compact-sm" px={6} {...control}>
            {label}
          </Button>
        ) : (
          <Anchor
            component="button"
            type="button"
            size="xs"
            c="var(--ink)"
            underline="always"
            className="inkLink"
            {...control}
          >
            {label}
          </Anchor>
        ))}
    </Stack>
  );
}

/**
 * Сомнительные в «Предложенных»: сколько видно из скольких и где остальные.
 *
 * **Число вкладки — всех предложенных, список — без сомнительных** (замечание владельца
 * 16.10.2026: «написано 14, а вижу только 3»). Судья прячет тех, кому советует отказ, чтобы
 * сверху стояли вероятные доноры, но вкладка считает и их. Переключатель «скрыто N» под
 * вкладками связи двух чисел не объяснял: строка над списком называет обе цифры и почему они
 * разные, а кнопка в конце списка открывает остальных там, где их ищут глазами.
 */

import { Button, Group, Stack, Switch, Text } from '@mantine/core';

import { plural } from '../format';

type NoteProps = {
  /** Предложенных всего — число вкладки. */
  total: number;
  /** Из них сомнительных: судья советует отказ. */
  hidden: number;
  /** Сомнительные сейчас в списке. */
  shown: boolean;
  onShow: (on: boolean) => void;
};

/** «Сайт» при числе, с глаголом и причастием в лад: «1 сайт … он скрыт». */
function sites(count: number): { noun: string; judged: string; hidden: string } {
  const one = plural(count, 'one', 'few', 'many') === 'one';
  return {
    noun: plural(count, 'сайт', 'сайта', 'сайтов'),
    judged: one ? 'сомнительным' : 'сомнительными',
    hidden: one ? 'он скрыт, а не выброшен' : 'они скрыты, а не выброшены',
  };
}

/** Слова строки над списком: обе цифры и почему они разные. */
export function noteWords(total: number, hidden: number, shown: boolean): string {
  if (hidden === 0) return 'Сомнительных нет: в списке все предложенные.';
  const word = sites(hidden);
  if (shown) {
    return `В списке — все предложенные, ${hidden} ${plural(hidden, 'сомнительный', 'сомнительных', 'сомнительных')} — внизу.`;
  }
  const visible = total - hidden;
  if (visible <= 0) {
    return `Все предложенные — ${hidden} ${word.noun} — судья считает ${word.judged}: ${word.hidden}.`;
  }
  return `В списке — ${visible} из ${total} предложенных: ещё ${hidden} ${word.noun} судья считает ${word.judged} — ${word.hidden}.`;
}

/** Строка над списком и переключатель; сомнительных нет и они не показаны — ничего. */
export function DoubtfulNote({ total, hidden, shown, onShow }: NoteProps) {
  if (hidden === 0 && !shown) return null;
  return (
    <Stack gap={6}>
      <Text size="sm">{noteWords(total, hidden, shown)}</Text>
      <Switch
        label="Показать сомнительные"
        checked={shown}
        onChange={(event) => onShow(event.currentTarget.checked)}
      />
    </Stack>
  );
}

type ShowProps = {
  /** Вкладка «Предложены»: на остальных сомнительных не прячут. */
  pending: boolean;
  shown: boolean;
  hidden: number;
  /** Сколько видимых строк ещё не показано: пока они есть, внизу — «Показать ещё». */
  rest: number;
  onShow: () => void;
};

/** Кнопка в конце списка: остальные — там, где их ищут. Видна, когда видимые строки
 *  показаны все, а сомнительные скрыты: две кнопки «показать» внизу спорили бы. */
export function ShowDoubtful({ pending, shown, hidden, rest, onShow }: ShowProps) {
  if (!pending || shown || hidden === 0 || rest > 0) return null;
  return (
    <Group justify="center" p="sm">
      <Button variant="light" onClick={onShow}>
        Показать {hidden} {plural(hidden, 'сомнительный', 'сомнительных', 'сомнительных')}
      </Button>
    </Group>
  );
}

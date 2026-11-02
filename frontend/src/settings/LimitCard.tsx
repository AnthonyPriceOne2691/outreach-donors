/**
 * Карточка лимита на экране «Расход» — одна на всех: Ahrefs, Hunter, модель, выдача, платный обход.
 *
 * Слово Anthony 02.11.2026: «там сейчас всё простынёй и непонятно». Лимиты шли сплошными строками
 * одной панели, и ответ на вопрос «сколько осталось» приходилось вычитывать. Теперь устройство у
 * всех одно: подпись, крупно — сколько осталось, из скольких, полоса, что это значит, когда сброс;
 * ниже подробности, внизу — что задано и «Изменить». Глаз находит одно и то же в одном месте.
 *
 * **Сетка не знает, сколько карточек** (`LimitGrid`): лимиты прибавляются, и новый счёт — ещё одна
 * карточка, а не правка раскладки. Подвал прижат к низу: кнопки соседних карточек стоят на одной
 * линии при разной длине подробностей.
 *
 * **Экран называет, а не считает**: число, доля и «осталось» приходят от сервера готовыми, тем же
 * правилом, что у тревог сторожа. Числа нет — вместо него строка, почему (`value` пусто).
 *
 * **Вложенный блок — `glassQuiet`**: карточка стоит внутри панели, второй слой стекла читался бы
 * грязью, и приглушённый тон на этой подложке уже подтянут к норме.
 */

import { Button, Card, Divider, Group, SimpleGrid, Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import { Meter } from '../components/Meter';
import { formatDateTime, formatTime } from '../format';

/** «проверено в 14:05» сегодня, «проверено 15.10.2026, 14:05» — раньше: старый снимок виден. */
export function checked(askedAt: string): string {
  const asked = new Date(askedAt);
  return asked.toDateString() === new Date().toDateString()
    ? `проверено в ${formatTime(askedAt)}`
    : `проверено ${formatDateTime(askedAt)}`;
}

/** «Изменить» карточки: `label` — имя кнопки для программы чтения с экрана («Изменить кап»), на
 *  самой кнопке короткое слово: три одинаковых длинных не помещались рядом со строкой подвала.
 *  `who` — кто это меняет, словами права. */
export interface Action {
  label: string;
  onClick: () => void;
  allowed: boolean;
  who: string;
}

/** Полоса карточки: сколько выбрано из скольких — и подпись для программы чтения с экрана. */
export interface Gauge {
  spent: number;
  cap: number;
  label: string;
}

interface Props {
  title: string;
  /** Подпись над числом: «Осталось», «На счету». */
  caption?: string | undefined;
  /** Крупное число; пусто — числа нет, и почему — говорит `note`. */
  value?: string | undefined;
  /** Из скольких и в чём: «из 50 000 юнитов». */
  of?: string | undefined;
  gauge?: Gauge | undefined;
  /** Что это значит: «выбрано 82%, упирается в лимит ключа». */
  note?: ReactNode;
  /** Когда сброс и когда спросили. */
  when?: ReactNode;
  /** Подробности — под чертой. */
  children?: ReactNode;
  /** Подвал — что задано: прижат к низу карточки, кнопка «Изменить» — справа от него. */
  footer?: ReactNode;
  action?: Action | undefined;
}

export function LimitCard({
  title,
  caption,
  value,
  of,
  gauge,
  note,
  when,
  children,
  footer,
  action,
}: Props) {
  return (
    <Card className="glassQuiet" p={{ base: 'md', sm: 'lg' }} role="group" aria-label={title}>
      <Stack gap="sm" h="100%">
        <Text fw={600}>{title}</Text>
        {value !== undefined && (
          <Stack gap={2}>
            {caption !== undefined && (
              <Text size="xs" c="dimmed">
                {caption}
              </Text>
            )}
            <Group gap={8} align="baseline" wrap="wrap">
              <Text fw={600} fz={28} lh={1.15}>
                {value}
              </Text>
              {of !== undefined && (
                <Text size="sm" c="dimmed">
                  {of}
                </Text>
              )}
            </Group>
          </Stack>
        )}
        {gauge !== undefined && <Meter spent={gauge.spent} cap={gauge.cap} label={gauge.label} />}
        {note !== undefined && (
          <Text size="sm" style={{ overflowWrap: 'anywhere' }}>
            {note}
          </Text>
        )}
        {when !== undefined && (
          <Text size="sm" c="dimmed">
            {when}
          </Text>
        )}
        {children !== undefined && (
          <>
            <Divider color="var(--hairline)" />
            <Stack gap={8}>{children}</Stack>
          </>
        )}
        {footer !== undefined && <LimitFooter action={action}>{footer}</LimitFooter>}
      </Stack>
    </Card>
  );
}

/** Подвал: что задано — слева, переносится; «Изменить» — справа и не сжимается. Без права вместо
 *  кнопки — строка, у кого оно: отсутствие права объясняется на месте (правило экранов). */
function LimitFooter({ action, children }: { action: Action | undefined; children: ReactNode }) {
  return (
    <Stack gap={4} mt="auto" pt={4}>
      <Group gap="sm" justify="space-between" wrap="nowrap">
        <Text size="sm" fw={500} miw={0} style={{ overflowWrap: 'anywhere' }}>
          {children}
        </Text>
        {action?.allowed === true && (
          <Button
            size="compact-sm"
            variant="default"
            className="press"
            aria-haspopup="dialog"
            aria-label={action.label}
            flex="none"
            onClick={action.onClick}
          >
            Изменить
          </Button>
        )}
      </Group>
      {action?.allowed === false && (
        <Text size="sm" c="dimmed">
          {action.who}
        </Text>
      )}
    </Stack>
  );
}

/** Строка подробностей карточки: мелко, длинный адрес не вылезает за край. */
export function LimitLine({ children, strong = false }: { children: ReactNode; strong?: boolean }) {
  return (
    <Text
      size="sm"
      {...(strong ? { fw: 500 } : { c: 'dimmed' })}
      style={{ overflowWrap: 'anywhere' }}
    >
      {children}
    </Text>
  );
}

/** Сетка карточек лимитов: столько колонок, сколько поместится, — карточек бывает сколько угодно,
 *  и на телефоне они идут одна под другой. */
export function LimitGrid({ children }: { children: ReactNode }) {
  return (
    <SimpleGrid cols={{ base: 1, md: 2, xl: 3 }} spacing="sm">
      {children}
    </SimpleGrid>
  );
}

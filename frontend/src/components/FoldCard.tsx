/**
 * Свёрнутая карточка: строка-кнопка с шевроном, тело — по нажатию.
 *
 * Так свёрнуто то, что смотрят не при каждом заходе: версии настроек агента
 * (`agent/AgentHistory`) и веса скоринга Этапа 2 на «Порогах»
 * (`settings/AdvertiserScoring`). Карточка одна, а не копии: кнопка, шеврон и ход
 * раскрытия у них одни. Открыта ли — решает экран (`open`, `onToggle`): «Пороги»
 * спрашивают сервер только по раскрытию.
 *
 * У кнопки `aria-expanded` и `aria-controls` (`docs/UI_RULES.md`): без них программа
 * чтения с экрана видит кнопку без состояния, а тест не отличит раскрытое от свёрнутого.
 *
 * Рядом с кнопкой — `aside`, например «i» с пояснением («Ждём ответа» на «Письмах»): внутри
 * кнопки значок был бы кнопкой в кнопке.
 */

import { Button, Card, Collapse, Group } from '@mantine/core';
import { IconChevronDown } from '@tabler/icons-react';
import { useId } from 'react';
import type { ReactNode } from 'react';

interface FoldCardProps {
  /** Надпись кнопки — она же имя раскрытия. */
  label: ReactNode;
  open: boolean;
  onToggle: () => void;
  /** Рядом с кнопкой, не внутри неё: «i» с пояснением. */
  aside?: ReactNode;
  children: ReactNode;
}

export function FoldCard({ label, open, onToggle, aside, children }: FoldCardProps) {
  const bodyId = useId();
  return (
    <Card className="glass" px="xl" py="md">
      <Group gap={aside === undefined ? 'md' : 4} wrap="nowrap">
        <Button
          variant="subtle"
          size="compact-md"
          px={6}
          leftSection={
            <IconChevronDown
              size={16}
              style={{
                transform: open ? 'rotate(180deg)' : 'none',
                transition: 'transform 200ms cubic-bezier(0.32, 0.72, 0, 1)',
              }}
            />
          }
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={onToggle}
        >
          {label}
        </Button>
        {aside}
      </Group>
      <Collapse id={bodyId} in={open} transitionDuration={220} transitionTimingFunction="ease">
        {children}
      </Collapse>
    </Card>
  );
}

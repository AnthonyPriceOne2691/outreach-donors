/**
 * Подтверждение действия с последствиями — маленьким окном у самой кнопки.
 *
 * Аудит экранов 09.10.2026: «Сбросить пароль» и выключатель учётки срабатывали с
 * первого нажатия. Окно на весь экран для такого — лишнее, а без подтверждения
 * промах стоит человеку входа. Правила (NN/g, GOV.UK): текст называет последствие,
 * на кнопке — глагол, а не «ОК»; кнопки «по умолчанию» нет — Enter ничего не
 * подтверждает сам, фокус встаёт на «Отмену».
 */

import { Button, Group, Popover, Stack, Text } from '@mantine/core';
import { useId, useState } from 'react';
import type { ReactElement, ReactNode } from 'react';

interface Props {
  /** Что случится — словами последствия. */
  message: ReactNode;
  /** Глагол на кнопке подтверждения: «Сбросить», «Отключить». */
  confirm: string;
  /** Последствие не отменить одним нажатием — кнопка красная. */
  danger?: boolean;
  onConfirm: () => void;
  /** Кнопка или переключатель, который просит подтверждения: получает `ask`. */
  children: (ask: () => void) => ReactElement;
}

export function ConfirmPopover({ message, confirm, danger = false, onConfirm, children }: Props) {
  const [opened, setOpened] = useState(false);
  // Окно называется своим вопросом, а не кнопкой, которая его открыла: так его и читает
  // программа чтения с экрана — «Отключить …? Учётка перестанет пускать…».
  const question = useId();
  return (
    <Popover
      opened={opened}
      onChange={setOpened}
      position="bottom"
      withArrow
      trapFocus
      returnFocus
      radius="lg"
      shadow="md"
      transitionProps={{ transition: 'pop', duration: 160 }}
    >
      <Popover.Target>{children(() => setOpened(true))}</Popover.Target>
      <Popover.Dropdown className="glassSolid" aria-labelledby={question}>
        <Stack gap="sm" maw={300}>
          <Text size="sm" id={question}>
            {message}
          </Text>
          <Group gap="xs" justify="flex-end" wrap="nowrap">
            <Button
              size="compact-sm"
              variant="default"
              data-autofocus
              onClick={() => setOpened(false)}
            >
              Отмена
            </Button>
            <Button
              size="compact-sm"
              color={danger ? 'red' : 'lagoon'}
              className="press"
              onClick={() => {
                setOpened(false);
                onConfirm();
              }}
            >
              {confirm}
            </Button>
          </Group>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}

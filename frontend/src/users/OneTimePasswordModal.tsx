/**
 * Разовый пароль показывается один раз.
 *
 * Поэтому окно не закрывается по щелчку мимо и по Esc: случайный щелчок
 * стоил бы нового сброса, а сотрудник в это время ждёт пароль. Закрыть
 * можно только кнопкой — то есть осознанно.
 */

import { Alert, Button, Code, CopyButton, Group, Modal, Stack, Text } from '@mantine/core';

import type { OneTimePassword } from '../api/types';

interface Props {
  issued: OneTimePassword | null;
  onClose: () => void;
}

export function OneTimePasswordModal({ issued, onClose }: Props) {
  return (
    <Modal
      opened={issued !== null}
      onClose={onClose}
      title="Разовый пароль"
      closeOnClickOutside={false}
      closeOnEscape={false}
      withCloseButton={false}
    >
      {issued !== null && (
        <Stack>
          <Text size="sm">
            Для учётки <b>{issued.user.email}</b>:
          </Text>
          <Code block fz="lg">
            {issued.password}
          </Code>
          <Alert color="yellow">{issued.note}</Alert>
          <Group justify="flex-end">
            <CopyButton value={issued.password}>
              {({ copied, copy }) => (
                <Button variant="default" onClick={copy}>
                  {copied ? 'Скопировано' : 'Скопировать'}
                </Button>
              )}
            </CopyButton>
            <Button onClick={onClose}>Записал, закрыть</Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}

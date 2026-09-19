/**
 * Смена своего пароля.
 *
 * Проверки длины и совпадения повторов сделаны и здесь — но не вместо
 * сервера, а до него: они экономят человеку круг, а решает всё равно
 * сервер. Требование одно — длина: правила про заглавные и цифры
 * заставляют писать `Password1!` и записывать его на бумажке.
 */

import { Alert, Button, Card, Center, PasswordInput, Stack, Text, Title } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { changePassword } from '../api/auth';
import { useSession } from './AuthProvider';

const MIN_LENGTH = 10;

export function ChangePasswordPage() {
  const { user, refresh } = useSession();
  const navigate = useNavigate();
  const [refusal, setRefusal] = useState<string | null>(null);

  const form = useForm({
    initialValues: { current: '', next: '', repeat: '' },
    validate: {
      current: (value) => (value.length > 0 ? null : 'Введите текущий пароль'),
      next: (value) =>
        value.length >= MIN_LENGTH
          ? null
          : `Не короче ${MIN_LENGTH} знаков: длина надёжнее сложности`,
      repeat: (value, values) => (value === values.next ? null : 'Пароли не совпадают'),
    },
  });

  const submit = form.onSubmit(async ({ current, next }) => {
    setRefusal(null);
    try {
      await changePassword(current, next);
      await refresh();
      notifications.show({ message: 'Пароль сменён', color: 'green' });
      void navigate('/', { replace: true });
    } catch (error) {
      setRefusal(error instanceof Error ? error.message : 'Сменить пароль не удалось');
    }
  });

  return (
    <Center h="100vh">
      <Card className="glass riseIn" w={440} p="xl">
        <form onSubmit={submit}>
          <Stack>
            <div>
              <Title order={3}>Смена пароля</Title>
              {user?.must_change_password === true && (
                <Text size="sm" c="dimmed">
                  Пароль выдан разовым. Пока он не сменён, остальное закрыто.
                </Text>
              )}
            </div>

            {refusal !== null && (
              <Alert color="red" title="Не сменили">
                {refusal}
              </Alert>
            )}

            <PasswordInput
              label="Текущий пароль"
              autoComplete="current-password"
              {...form.getInputProps('current')}
            />
            <PasswordInput
              label="Новый пароль"
              description="Три-четыре слова подряд надёжнее, чем знаки разного регистра"
              autoComplete="new-password"
              {...form.getInputProps('next')}
            />
            <PasswordInput
              label="Новый пароль ещё раз"
              autoComplete="new-password"
              {...form.getInputProps('repeat')}
            />
            <Button
              type="submit"
              size="md"
              className="press"
              variant="gradient"
              gradient={{ from: 'lagoon.5', to: 'lagoon.7', deg: 135 }}
              loading={form.submitting}
            >
              Сменить
            </Button>
          </Stack>
        </form>
      </Card>
    </Center>
  );
}

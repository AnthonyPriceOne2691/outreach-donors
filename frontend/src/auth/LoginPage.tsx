/**
 * Вход.
 *
 * Отказ показывается ровно тем текстом, который прислал сервер:
 * «неверная почта или пароль» — намеренно один на все причины, и
 * дописывать к нему догадки («проверьте почту») значит подсказывать
 * тому, кто подбирает.
 *
 * Отдельно разобран отказ по счётчику попыток: это не ошибка ввода,
 * а «подождите», и человеку надо сказать сколько.
 */

import {
  Alert,
  Button,
  Card,
  Center,
  PasswordInput,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { TooManyAttemptsError } from '../api/client';
import { useSession } from './AuthProvider';

interface FromState {
  from?: string;
}

export function LoginPage() {
  const { signIn } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [refusal, setRefusal] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(false);

  const form = useForm({
    initialValues: { email: '', password: '' },
    validate: {
      email: (value) => (value.trim().length > 0 ? null : 'Введите почту'),
      password: (value) => (value.length > 0 ? null : 'Введите пароль'),
    },
  });

  const submit = form.onSubmit(async ({ email, password }) => {
    setRefusal(null);
    setWaiting(false);
    try {
      const user = await signIn(email, password);
      // Разовый пароль — сразу на смену: остальное всё равно закрыто.
      const goTo = user.must_change_password
        ? '/password'
        : ((location.state as FromState | null)?.from ?? '/');
      void navigate(goTo, { replace: true });
    } catch (error) {
      if (error instanceof TooManyAttemptsError) {
        setWaiting(true);
        setRefusal(error.message);
        return;
      }
      setRefusal(error instanceof Error ? error.message : 'Войти не удалось');
    }
  });

  return (
    <Center h="100vh">
      <Card className="glass riseIn" w={400} p="xl">
        <form onSubmit={submit}>
          <Stack>
            <div>
              <Title order={3}>Доноры и цены</Title>
              <Text size="sm" c="dimmed">
                Вход для сотрудников
              </Text>
            </div>

            {refusal !== null && (
              <Alert color={waiting ? 'yellow' : 'red'} title={waiting ? 'Подождите' : 'Не вошли'}>
                {refusal}
              </Alert>
            )}

            <TextInput label="Почта" autoComplete="username" {...form.getInputProps('email')} />
            <PasswordInput
              label="Пароль"
              autoComplete="current-password"
              {...form.getInputProps('password')}
            />
            <Button
              type="submit"
              size="md"
              className="press"
              variant="gradient"
              gradient={{ from: 'lagoon.5', to: 'lagoon.7', deg: 135 }}
              loading={form.submitting}
            >
              Войти
            </Button>
          </Stack>
        </form>
      </Card>
    </Center>
  );
}

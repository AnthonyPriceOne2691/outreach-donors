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
 *
 * **Сеанс, оборванный сервером, назван** (проверка QA 10.10.2026): учётку
 * отключили посреди работы — и человек молча оказывался здесь. Догадка
 * «учётку могли отключить» подсказкой подбирающему не служит: её видит
 * только тот, у кого в этой вкладке был живой сеанс. С проверки прода
 * 10.10.2026 сеанс обрывает и сброс пароля — вход называет и его. С аудита
 * 20.11.2026 — и простой: причину вход показывает словами сервера («Сеанс
 * закончился: долго не было действий — войдите снова»), если он её назвал.
 *
 * **У учётки с кодом пароль — только первый шаг** (23.11.2026): сервер отвечает вызовом ко
 * второму (`SecondStepError`), и вход продолжается здесь же — кодом из приложения (`CodeStep`)
 * или, если роль требует код, а он не включён, его включением (`EnrollStep`).
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

import { SecondStepError, TooManyAttemptsError } from '../api/client';
import type { LoginStep } from '../api/client';
import type { Me, SignedIn } from '../api/types';
import { SERVICE_NAME } from '../brand';
import { useSession } from './AuthProvider';
import { CodeStep } from './CodeStep';
import { EnrollStep } from './EnrollStep';

interface FromState {
  from?: string;
}

interface Pending {
  step: LoginStep;
  challenge: string;
}

interface NextStepProps {
  pending: Pending;
  accept: (opened: SignedIn) => Me;
  enter: (user: Me) => void;
  onRestart: () => void;
}

/** Второй шаг после верного пароля: код — или включение кода, если роль его требует. */
function NextStep({ pending, accept, enter, onRestart }: NextStepProps) {
  if (pending.step === 'code') {
    return (
      <CodeStep
        challenge={pending.challenge}
        onSignedIn={(opened) => enter(accept(opened))}
        onRestart={onRestart}
      />
    );
  }
  return <EnrollStep challenge={pending.challenge} onDone={enter} onRestart={onRestart} />;
}

/** Включение кода шире входа: ключ, ссылка и коды восстановления в 400 px теснятся. */
function cardWidth(pending: Pending | null): number {
  return pending?.step === 'enroll' ? 480 : 400;
}

export function LoginPage() {
  const { signIn, accept, cutOff } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [refusal, setRefusal] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);

  /** Вошли: разовый пароль — сразу на смену, остальное всё равно закрыто. */
  const enter = (user: Me) => {
    const goTo = user.must_change_password
      ? '/password'
      : ((location.state as FromState | null)?.from ?? '/');
    void navigate(goTo, { replace: true });
  };

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
      enter(await signIn(email, password));
    } catch (error) {
      if (error instanceof SecondStepError) {
        // Пароль подошёл — дальше второй шаг; набранный пароль на экране больше не нужен.
        form.setFieldValue('password', '');
        setPending({ step: error.step, challenge: error.challenge });
        return;
      }
      if (error instanceof TooManyAttemptsError) {
        setWaiting(true);
        setRefusal(error.message);
        return;
      }
      setRefusal(error instanceof Error ? error.message : 'Войти не удалось');
    }
  });

  return (
    // Поле по краям — на телефоне карточка иначе встаёт вплотную к рамке экрана.
    <Center mih="100dvh" px="md" py="xl">
      <Card className="glass riseIn" w="100%" maw={cardWidth(pending)} p="xl">
        {pending !== null && (
          <NextStep
            pending={pending}
            accept={accept}
            enter={enter}
            onRestart={() => setPending(null)}
          />
        )}
        <form onSubmit={submit} hidden={pending !== null}>
          <Stack>
            <div>
              <Title order={3}>{SERVICE_NAME}</Title>
              <Text size="sm" c="dimmed">
                Вход для сотрудников
              </Text>
            </div>

            {cutOff !== null && (
              <Alert color="yellow" title="Сеанс закончился">
                {cutOff === '' ? (
                  'Войдите снова; если не пускает, пароль могли сбросить или учётку отключить — спросите админа.'
                ) : (
                  <Stack gap={4}>
                    <Text size="sm">{cutOff}</Text>
                    <Text size="sm">
                      Если не пускает, пароль могли сбросить или учётку отключить — спросите админа.
                    </Text>
                  </Stack>
                )}
              </Alert>
            )}

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
              mt="md"
              className="press"
              variant="gradient"
              /* Ступени глубокие: на светлых белый текст не читается. */
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

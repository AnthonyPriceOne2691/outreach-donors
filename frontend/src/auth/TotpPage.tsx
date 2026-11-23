/**
 * «Вход с кодом» своей учётки — из меню у почты (23.11.2026).
 *
 * Код не включён — включить (`TurnOn`): пароль, ключ в приложение и первый код, коды
 * восстановления. Включение гасит прежние пропуска учётки — на других устройствах войдут заново,
 * уже с кодом; этот сеанс берёт новый пропуск из ответа.
 *
 * Код включён — выключить паролем и кодом (`TurnOff`). Роль требует код — выключить нельзя:
 * сменить телефон — попросить админа сбросить вход с кодом, и при следующем входе учётка включит
 * его заново.
 */

import { Badge, Button, Card, Center, Group, Stack, Text, Title } from '@mantine/core';
import { useState } from 'react';
import { NavigationType, useNavigate, useNavigationType } from 'react-router-dom';

import { SERVICE_NAME } from '../brand';
import { FIELD_GAP } from '../components/formRhythm';
import { useSession } from './AuthProvider';
import { TurnOff, TurnOn } from './TotpSwitch';

export function TotpPage() {
  const { user } = useSession();
  const navigate = useNavigate();
  // Как у смены пароля: пришли ссылкой из меню — шаг назад вернёт туда же, иначе — на «Обзор».
  const cameByLink = useNavigationType() === NavigationType.Push;
  const leave = () => void (cameByLink ? navigate(-1) : navigate('/'));
  // Что делать, решает состояние на входе в экран: включение берёт новый пропуск с карточкой
  // «код включён» раньше, чем человек сохранит коды восстановления, — и экран не должен
  // смениться на «выключить» посреди показа кодов.
  const [wasOn] = useState(() => user?.totp_enabled === true);
  if (user === null) return null;

  let body;
  if (!wasOn) body = <TurnOn onLeave={leave} />;
  else if (user.totp_required)
    body = (
      <Stack gap={FIELD_GAP}>
        <Text size="sm">
          Для вашей роли вход с кодом обязателен — выключить его нельзя. Сменили или потеряли
          телефон — попросите админа сбросить вход с кодом: при следующем входе включите его заново.
        </Text>
        <Button variant="default" size="md" className="press" onClick={leave}>
          Назад
        </Button>
      </Stack>
    );
  else body = <TurnOff onLeave={leave} />;

  return (
    <Center mih="100dvh" px="md" py="xl">
      <Card className="glass riseIn" w="100%" maw={480} p="xl">
        <Stack gap={FIELD_GAP}>
          <div>
            <Text size="sm" c="dimmed">
              {SERVICE_NAME}
            </Text>
            <Group gap="sm" align="center">
              <Title order={3}>Вход с кодом</Title>
              <Badge variant="light" color={user.totp_enabled ? 'green' : 'gray'}>
                {user.totp_enabled ? 'включён' : 'выключен'}
              </Badge>
            </Group>
          </div>
          {body}
        </Stack>
      </Card>
    </Center>
  );
}

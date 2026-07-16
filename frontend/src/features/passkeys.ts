type PasskeyDescriptorJson = Omit<PublicKeyCredentialDescriptor, "id"> & {
  id: string;
};

type PasskeyCreationOptionsJson =
  Omit<PublicKeyCredentialCreationOptions, "challenge" | "excludeCredentials" | "user"> & {
    challenge: string;
    excludeCredentials?: PasskeyDescriptorJson[];
    user: Omit<PublicKeyCredentialUserEntity, "id"> & { id: string };
  };

type PasskeyRequestOptionsJson =
  Omit<PublicKeyCredentialRequestOptions, "allowCredentials" | "challenge"> & {
    allowCredentials?: PasskeyDescriptorJson[];
    challenge: string;
  };

export type PasskeyRegistrationCredentialJson = {
  id: string;
  rawId: string;
  type: string;
  authenticatorAttachment?: string | null;
  response: {
    attestationObject: string;
    clientDataJSON: string;
    transports?: string[];
  };
  clientExtensionResults: AuthenticationExtensionsClientOutputs;
};

export type PasskeyAuthenticationCredentialJson = {
  id: string;
  rawId: string;
  type: string;
  authenticatorAttachment?: string | null;
  response: {
    authenticatorData: string;
    clientDataJSON: string;
    signature: string;
    userHandle: string | null;
  };
  clientExtensionResults: AuthenticationExtensionsClientOutputs;
};

export function isPasskeySupported(): boolean {
  return (
    typeof window !== "undefined"
    && typeof PublicKeyCredential !== "undefined"
    && typeof navigator.credentials?.create === "function"
    && typeof navigator.credentials?.get === "function"
  );
}

export async function createPasskey(
  publicKey: unknown,
): Promise<PasskeyRegistrationCredentialJson> {
  if (!isPasskeySupported()) {
    throw new Error("Passkeys are not supported by this browser.");
  }
  const credential = await navigator.credentials.create({
    publicKey: decodeCreationOptions(publicKey),
  });
  if (!(credential instanceof PublicKeyCredential)) {
    throw new Error("Passkey registration did not return a credential.");
  }
  if (!(credential.response instanceof AuthenticatorAttestationResponse)) {
    throw new Error("Passkey registration returned an invalid response.");
  }
  return registrationCredentialToJson(credential, credential.response);
}

export async function getPasskey(
  publicKey: unknown,
  signal?: AbortSignal,
): Promise<PasskeyAuthenticationCredentialJson> {
  if (!isPasskeySupported()) {
    throw new Error("Passkeys are not supported by this browser.");
  }
  const request: CredentialRequestOptions = {
    publicKey: decodeRequestOptions(publicKey),
  };
  if (signal) request.signal = signal;
  const credential = await navigator.credentials.get(request);
  if (!(credential instanceof PublicKeyCredential)) {
    throw new Error("Passkey sign-in did not return a credential.");
  }
  if (!(credential.response instanceof AuthenticatorAssertionResponse)) {
    throw new Error("Passkey sign-in returned an invalid response.");
  }
  return authenticationCredentialToJson(credential, credential.response);
}

function decodeCreationOptions(publicKey: unknown): PublicKeyCredentialCreationOptions {
  const options = publicKey as PasskeyCreationOptionsJson;
  const {
    challenge,
    excludeCredentials,
    user,
    ...rest
  } = options;
  const decoded: PublicKeyCredentialCreationOptions = {
    ...rest,
    challenge: base64UrlToArrayBuffer(challenge),
    user: {
      ...user,
      id: base64UrlToArrayBuffer(user.id),
    },
  };
  if (excludeCredentials) {
    decoded.excludeCredentials = excludeCredentials.map(decodeDescriptor);
  }
  return decoded;
}

function decodeRequestOptions(publicKey: unknown): PublicKeyCredentialRequestOptions {
  const options = publicKey as PasskeyRequestOptionsJson;
  const {
    allowCredentials,
    challenge,
    ...rest
  } = options;
  const decoded: PublicKeyCredentialRequestOptions = {
    ...rest,
    challenge: base64UrlToArrayBuffer(challenge),
  };
  if (allowCredentials) {
    decoded.allowCredentials = allowCredentials.map(decodeDescriptor);
  }
  return decoded;
}

function decodeDescriptor(descriptor: PasskeyDescriptorJson): PublicKeyCredentialDescriptor {
  return {
    ...descriptor,
    id: base64UrlToArrayBuffer(descriptor.id),
  };
}

function registrationCredentialToJson(
  credential: PublicKeyCredential,
  response: AuthenticatorAttestationResponse,
): PasskeyRegistrationCredentialJson {
  const result: PasskeyRegistrationCredentialJson = {
    id: credential.id,
    rawId: arrayBufferToBase64Url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment,
    response: {
      attestationObject: arrayBufferToBase64Url(response.attestationObject),
      clientDataJSON: arrayBufferToBase64Url(response.clientDataJSON),
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  };
  if (typeof response.getTransports === "function") {
    result.response.transports = response.getTransports();
  }
  return result;
}

function authenticationCredentialToJson(
  credential: PublicKeyCredential,
  response: AuthenticatorAssertionResponse,
): PasskeyAuthenticationCredentialJson {
  return {
    id: credential.id,
    rawId: arrayBufferToBase64Url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment,
    response: {
      authenticatorData: arrayBufferToBase64Url(response.authenticatorData),
      clientDataJSON: arrayBufferToBase64Url(response.clientDataJSON),
      signature: arrayBufferToBase64Url(response.signature),
      userHandle: response.userHandle
        ? arrayBufferToBase64Url(response.userHandle)
        : null,
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

function base64UrlToArrayBuffer(value: string): ArrayBuffer {
  const normalized = value.replaceAll("-", "+").replaceAll("_", "/");
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
  const binary = window.atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes.buffer;
}

function arrayBufferToBase64Url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return window.btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}
